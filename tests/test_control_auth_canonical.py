"""base64url canonical-form rejection in control-plane tokens.

`HMACTokenAuth` encoded tokens as unpadded base64url. A 32-byte SHA-256
signature becomes 43 characters: 43 x 6 = 258 bits carrying 256 bits of data,
so the **final character's low two bits are discarded** by
`base64.urlsafe_b64decode`. Four different strings therefore decode to the same
signature, and a token whose last character had only those bits altered passed
`verify()` — the HMAC comparison never saw the change, because the change was
not part of the bytes it compared.

That is a real defect with three consequences:

* one session has four distinct token strings;
* anything keyed on the token string (replay cache, audit log, revocation list)
  can be forked or bypassed by editing two bits;
* the tamper test that should have caught it passed or failed depending on the
  random session id — a ~1-in-4 flake in the suite, observed twice.

`verify` now rejects non-canonical encodings, which is the standard rule for
JWT-style segments.
"""

from __future__ import annotations

import base64

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.control_auth import (  # noqa: E402
    ExpiredToken,
    HMACTokenAuth,
    InvalidToken,
    Role,
)

SECRET = b"k" * 48
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _non_canonical_variants(sig: str) -> list[str]:
    """The three sibling final characters that decode to the same bytes.

    A canonical 43rd character has alphabet index divisible by 4 (its low two
    bits are zero). Indices base+1, base+2 and base+3 carry identical payload
    bits and are discarded on decode, so all four decode to the same 32 bytes.
    """
    idx = ALPHABET.index(sig[-1])
    assert idx % 4 == 0, "issued signature was not canonically encoded"
    return [ALPHABET[idx + n] for n in (1, 2, 3)]


def test_all_final_character_variants_are_rejected() -> None:
    """Every non-canonical form of a valid token must fail verification."""
    auth = HMACTokenAuth(SECRET)
    accepted_wrong: list[str] = []
    variants_tested = 0
    for _ in range(120):
        token = auth.issue("alice", [Role.OPERATOR], ttl_seconds=30)
        head, _, sig = token.rpartition(".")
        for cand in _non_canonical_variants(sig):
            forged = f"{head}.{sig[:-1]}{cand}"
            variants_tested += 1
            try:
                auth.verify(forged)
            except InvalidToken:
                continue
            except ExpiredToken:  # pragma: no cover - real clock, 30s ttl
                accepted_wrong.append(forged)
                continue
            accepted_wrong.append(forged)
    assert variants_tested == 360
    assert not accepted_wrong, (
        f"{len(accepted_wrong)} non-canonical token(s) verified, e.g. {accepted_wrong[:1]}"
    )


def test_low_bit_flip_is_rejected() -> None:
    """The exact mutation the flaky tamper test performed."""
    auth = HMACTokenAuth(SECRET)
    rejected = 0
    for _ in range(120):
        token = auth.issue("bob", [Role.OPERATOR], ttl_seconds=30)
        head, _, sig = token.rpartition(".")
        last = token[-1]
        mutated = f"{head}.{sig[:-1]}{('A' if last != 'A' else 'B')}"
        try:
            auth.verify(mutated)
        except InvalidToken:
            rejected += 1
    assert rejected == 120, (
        f"only {rejected}/120 low-bit flips were rejected; the encoder is probably "
        "back to a non-canonical form"
    )


def test_four_string_forms_decode_to_one_signature() -> None:
    """Documents the shape of the defect: one session, four token strings.

    Without this the canonical check could be "fixed" by breaking decoding.
    """
    import base64

    from engine.control_auth import _b64encode

    def _raw_decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(
            value.encode("ascii") + b"=" * (-len(value) % 4)
        )

    auth = HMACTokenAuth(SECRET)
    token = auth.issue("gina", [Role.OPERATOR], ttl_seconds=30)
    _, _, sig = token.rpartition(".")
    forms = [sig[:-1] + c for c in (sig[-1], *_non_canonical_variants(sig))]
    decoded = {_raw_decode(f) for f in forms}
    assert len(decoded) == 1, "the four forms should be indistinguishable after decode"
    assert sum(1 for f in forms if _b64encode(_raw_decode(f)) == f) == 1, (
        "exactly one of the four forms may be canonical"
    )


def test_canonical_token_still_verifies() -> None:
    auth = HMACTokenAuth(SECRET)
    for _ in range(50):
        session = auth.verify(auth.issue("carol", [Role.OPERATOR], ttl_seconds=30))
        assert session.subject == "carol"


def test_signature_with_padding_is_rejected() -> None:
    """`=` padding is not part of the canonical unpadded form."""
    auth = HMACTokenAuth(SECRET)
    token = auth.issue("dave", [Role.OPERATOR], ttl_seconds=30)
    head, _, sig = token.rpartition(".")
    padded = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    with pytest.raises(InvalidToken):
        auth.verify(f"{head}.{base64.urlsafe_b64encode(padded).decode()}")


def test_malformed_token_is_rejected() -> None:
    auth = HMACTokenAuth(SECRET)
    for bad in ("", "a", "a.b", "not-a-token", "a.b.c.d"):
        with pytest.raises(InvalidToken):
            auth.verify(bad)


def test_expired_token_still_expires() -> None:
    clock = [1_000.0]
    auth = HMACTokenAuth(SECRET, clock=lambda: clock[0])
    token = auth.issue("erin", [Role.OPERATOR], ttl_seconds=30)
    assert auth.verify(token).subject == "erin"
    clock[0] = 1_031.0
    with pytest.raises(ExpiredToken):
        auth.verify(token)


def test_payload_tamper_is_rejected() -> None:
    """The payload segment gets the same canonical check."""
    auth = HMACTokenAuth(SECRET)
    token = auth.issue("frank", [Role.OPERATOR], ttl_seconds=30)
    head, _, sig = token.rpartition(".")
    segment, _, _ = head.partition(".")
    flipped = ("B" if segment[-1] != "B" else "C") + segment[1:]
    with pytest.raises(InvalidToken):
        auth.verify(f"{segment and flipped}.{sig}")