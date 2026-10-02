"""Encryption for operator secrets at rest.

The ``operator_secrets`` table lives inside ``data/storage/records.db`` — the
same SQLite file as the trade and event journal. Storing exchange API keys
there in cleartext means a single copied ``.db`` file (or its ``-wal`` sidecar,
which carries the same plaintext) hands over live credentials, and that file is
3+ GB in a running install.

Values are stored as ``v1:<backend>:<base64>`` and encrypted with:

1. **DPAPI** (``CryptProtectData``, user scope) on Windows — OS key management,
   no key to rotate, and the ciphertext is bound to the operator's account.
2. **AES-256-GCM** elsewhere, keyed by ``TRADING_SECRET_STORE_KEY``.

If neither backend is available, persistence **fails closed**: values are not
written at all rather than silently written in cleartext. Setting
``TRADING_SECRET_STORE_PLAINTEXT=1`` restores the old behaviour for a
throwaway environment and logs a CRITICAL every time it is used.

Legacy cleartext rows are read transparently and re-encrypted on the next write,
so upgrading does not require a migration step.
"""

from __future__ import annotations

import base64
import ctypes
import os
import struct
from typing import Protocol

__all__ = [
    "SecretCipher",
    "get_cipher",
    "encrypt_secret",
    "decrypt_secret",
    "is_encrypted",
    "backend_name",
    "PLAIN_ENV",
    "KEY_ENV",
    "SecretBackendUnavailable",
]

PLAIN_ENV = "TRADING_SECRET_STORE_PLAINTEXT"
KEY_ENV = "TRADING_SECRET_STORE_KEY"
_PREFIX = "v1:"


class SecretBackendUnavailable(RuntimeError):
    """No usable encryption backend and plaintext was not explicitly allowed."""


class SecretCipher(Protocol):
    name: str

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, token: str) -> str: ...


# --------------------------------------------------------------------------
# Windows DPAPI
# --------------------------------------------------------------------------

_DPAPI_KEY = b"trading_bot.operator_secrets.v1"
#: CRYPTPROTECT_UI_FORBIDDEN
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    """Windows ``DATA_BLOB``.  Must be declared explicitly: with the default
    ctypes return type the 64-bit ``pbData`` pointer gets truncated and
    ``CryptProtectData`` fails with a bare ``OSError``."""

    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


class _DpapiCipher:
    """Windows user-scope DPAPI.

    No optional-entropy blob is passed: DPAPI already binds the ciphertext to
    the operator's Windows account and machine, which is the property we need,
    and marshalling the entropy pointer through ctypes is a reliable way to get
    ``ERROR_INVALID_DATA``.
    """

    name = "dpapi"

    def __init__(self) -> None:
        from ctypes import wintypes

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL

        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL

        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        self._crypt32 = crypt32
        self._kernel32 = kernel32
        # Fail at construction if the API is unusable, so the caller can fall
        # through to the portable backend instead of failing at first write.
        probe = self.encrypt("probe")
        if self.decrypt(probe) != "probe":  # pragma: no cover - defensive
            raise OSError("DPAPI round-trip failed")

    def _run(self, data: bytes, *, protect: bool) -> bytes:
        src, src_buf = _to_blob(data)
        out = _DataBlob()
        if protect:
            ok = self._crypt32.CryptProtectData(
                ctypes.byref(src),
                "trading_bot operator secrets",
                None,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out),
            )
        else:
            ok = self._crypt32.CryptUnprotectData(
                ctypes.byref(src),
                None,
                None,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out),
            )
        del src_buf
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return _from_blob(out)
        finally:
            self._kernel32.LocalFree(out.pbData)

    def encrypt(self, plaintext: str) -> str:
        return base64.b64encode(
            self._run(plaintext.encode("utf-8"), protect=True)
        ).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._run(base64.b64decode(token), protect=False).decode("utf-8")


# --------------------------------------------------------------------------
# Portable AES-256-GCM
# --------------------------------------------------------------------------


class _AesGcmCipher:
    name = "aes-gcm"

    def __init__(self, key: bytes) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        blob = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + blob).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token)
        return self._aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")


class _PlaintextCipher:
    """Escape hatch. Logs loudly; never the default."""

    name = "plaintext"

    def encrypt(self, plaintext: str) -> str:
        return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return base64.b64decode(token).decode("utf-8")


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

_cached: SecretCipher | None = None
_warned = False


def _derive_key(material: str) -> bytes:
    import hashlib

    return hashlib.sha256(material.encode("utf-8")).digest()


def get_cipher() -> SecretCipher:
    """Return the process-wide cipher, or raise :class:`SecretBackendUnavailable`."""
    global _cached, _warned
    if _cached is not None:
        return _cached

    if os.name == "nt":
        try:
            _cached = _DpapiCipher()
            return _cached
        except Exception:  # noqa: BLE001 - fall through to portable backend
            pass

    material = str(os.environ.get(KEY_ENV) or "").strip()
    if len(material) >= 16:
        try:
            _cached = _AesGcmCipher(_derive_key(material))
            return _cached
        except Exception:  # noqa: BLE001 - fall through
            pass

    if str(os.environ.get(PLAIN_ENV) or "").strip().lower() in {"1", "true", "yes"}:
        if not _warned:
            _warned = True
            import logging

            logging.getLogger("secrets").critical(
                "%s is set: operator secrets are stored with base64 only, NOT "
                "encrypted. Never do this outside a throwaway environment.",
                PLAIN_ENV,
            )
        _cached = _PlaintextCipher()
        return _cached

    raise SecretBackendUnavailable(
        "no secret encryption backend available. Set "
        f"{KEY_ENV} (>=16 chars) for AES-256-GCM, run on Windows for DPAPI, "
        f"or set {PLAIN_ENV}=1 to accept cleartext storage deliberately."
    )


def is_encrypted(value: str) -> bool:
    return str(value or "").startswith(_PREFIX)


def backend_name() -> str:
    try:
        return get_cipher().name
    except SecretBackendUnavailable:
        return "none"


def encrypt_secret(plaintext: str) -> str:
    """Return the storable form. Raises when no backend is available."""
    text = str(plaintext or "")
    if not text:
        return ""
    if is_encrypted(text):
        return text
    cipher = get_cipher()
    return f"{_PREFIX}{cipher.name}:{cipher.encrypt(text)}"


def decrypt_secret(stored: str) -> str:
    """Read a stored value, tolerating legacy cleartext rows."""
    text = str(stored or "")
    if not text:
        return ""
    if not is_encrypted(text):
        return text
    _, backend, token = text.split(":", 2)
    try:
        cipher = get_cipher()
    except SecretBackendUnavailable as exc:
        raise SecretBackendUnavailable(
            f"cannot decrypt stored secrets ({backend}): {exc}"
        ) from exc
    if cipher.name != backend:
        raise SecretBackendUnavailable(
            f"stored secrets use {backend} but this process has {cipher.name}; "
            f"set {KEY_ENV} to the original value or restore the original platform"
        )
    return cipher.decrypt(token)


_ = struct  # keep the import meaningful for future format changes
