"""Shared-secret session token for the Operator BFF (browser <-> :8787).

The BFF is a localhost HTTP service that can halt, re-arm and mutate a live
trading book.  It therefore needs its own authentication layer, separate from
the engine's HMAC command tokens, because the engine socket (TCP :8765) and the
BFF have different transports and different threat surfaces:

* **CSRF / drive-by web pages.**  ``POST /api/command/HALT`` with no body is a
  *simple* request, so any page the operator visits can trigger it.  Requiring
  an ``X-Operator-Token`` header makes the request non-simple, which forces a
  CORS preflight that our own middleware can refuse.
* **WebSocket hijacking.**  WebSockets ignore CORS entirely, so the handshake
  must check ``Origin`` against an exact allow-list.
* **Other local processes.**  A same-user process can read the token file; that
  is an accepted limitation of a single-user desktop tool and is documented
  rather than papered over.  The token file is created with 0600 and the token
  is never logged after boot.

Resolution order: ``OPERATOR_API_TOKEN`` env, then the token file, then a fresh
token is generated and persisted.
"""

from __future__ import annotations

import hmac
import os
import secrets
import stat
from pathlib import Path
from typing import Final

__all__ = [
    "TOKEN_ENV",
    "TOKEN_HEADER",
    "DEFAULT_ALLOWED_ORIGINS",
    "OperatorSession",
    "get_session",
    "extract_bearer",
]

TOKEN_ENV: Final = "OPERATOR_API_TOKEN"
TOKEN_HEADER: Final = "x-operator-token"
_QUERY_PREFIX: Final = "operator_token="
_MIN_TOKEN_BYTES: Final = 24

#: Exact origins only.  A regex over ``localhost(:\d+)?`` would trust every
#: other dev server on the machine, which is exactly the class of thing an
#: operator runs without thinking.
DEFAULT_ALLOWED_ORIGINS: Final[frozenset[str]] = frozenset(
    {
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://127.0.0.1:8787",
        "http://localhost:8787",
        "https://tauri.localhost",
        "http://tauri.localhost",
        "tauri://localhost",
    }
)

#: Requests without an ``Origin`` header (native clients, Tauri, curl) are not
#: browsers and cannot be CSRF vectors, so they are allowed through the origin
#: gate and must satisfy the token gate instead.
_ORIGINLESS_ALLOWED: Final = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _harden(path: Path) -> None:
    """Best-effort 0600 on the token file (no-op on Windows)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


class OperatorSession:
    """Holds the BFF session token and performs constant-time verification."""

    def __init__(
        self,
        *,
        token: str | None = None,
        token_path: Path | None = None,
        allowed_origins: frozenset[str] | None = None,
    ) -> None:
        self._allowed_origins = allowed_origins or DEFAULT_ALLOWED_ORIGINS
        self._token_path = token_path or (_repo_root() / "data" / "ui" / "operator_token")
        self._generated = False
        self._token = ""
        self.token = token or ""

    # -- token lifecycle ---------------------------------------------------

    @property
    def token(self) -> str:
        return self._token

    @token.setter
    def token(self, value: str) -> None:
        candidate = str(value or "").strip()
        env = str(os.environ.get(TOKEN_ENV) or "").strip()
        if env:
            self._token = env
            self._generated = False
            return
        if candidate:
            self._token = candidate
            self._generated = False
            return
        if self._read_file():
            return
        self._token = secrets.token_urlsafe(32)
        self._generated = True
        self._write_file()

    def _read_file(self) -> bool:
        try:
            stored = self._token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if len(stored) < _MIN_TOKEN_BYTES:
            return False
        self._token = stored
        self._generated = False
        return True

    def _write_file(self) -> None:
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create so two BFFs cannot clobber each other's token.
            fd = os.open(self._token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, self._token.encode("utf-8"))
            finally:
                os.close(fd)
        except FileExistsError:
            # Lost the race; adopt the winner's token so the UI still works.
            if self._read_file():
                return
        except OSError:
            # Unwritable data dir: keep the in-memory token so this process is
            # still usable; the next boot will mint a different one.
            return
        _harden(self._token_path)

    # -- verification ------------------------------------------------------

    def verify(self, candidate: str | None) -> bool:
        supplied = str(candidate or "").strip()
        if not supplied or not self._token:
            return False
        return hmac.compare_digest(supplied, self._token)

    def origin_allowed(self, origin: str | None) -> bool:
        """Exact-match origin check for CORS and the WebSocket handshake."""
        if origin is None or origin == "":
            return _ORIGINLESS_ALLOWED
        if origin in self._allowed_origins:
            return True
        # Tauri may report the origin without a scheme in some configurations.
        return origin in {o.split("://", 1)[-1] for o in self._allowed_origins}

    @property
    def allowed_origins(self) -> list[str]:
        return sorted(self._allowed_origins)

    @property
    def generated(self) -> bool:
        """True when this process minted a fresh token (log it once, at boot)."""
        return self._generated

    def status(self) -> dict[str, object]:
        return {
            "token_source": "env" if os.environ.get(TOKEN_ENV) else (
                "generated" if self._generated else "file"
            ),
            "token_path": str(self._token_path),
            "allowed_origins": self.allowed_origins,
        }


_session: OperatorSession | None = None


def get_session() -> OperatorSession:
    """Process-wide singleton."""
    global _session
    if _session is None:
        _session = OperatorSession()
    return _session


def extract_bearer(value: str | None) -> str:
    """Accept both ``Authorization: Bearer <t>`` and the raw token header."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def token_from_query(query: str | None) -> str:
    """Pull ``operator_token`` out of a WebSocket query string.

    WebSocket handshakes cannot carry custom headers from the browser, so the
    token travels as a query parameter.  It is still gated by the exact-origin
    check, and the URL is not logged.
    """
    from urllib.parse import parse_qs

    raw = str(query or "").lstrip("?")
    if not raw:
        return ""
    values = parse_qs(raw, keep_blank_values=False).get("operator_token")
    return str(values[0]) if values else ""


_ = _QUERY_PREFIX  # kept for documentation of the parameter name
