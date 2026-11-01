"""Single-password authentication.

* The password is stored only as a scrypt hash in ``<data_dir>/auth.json`` (mode 0600).
* A logged-in browser holds a signed cookie ``nt_session`` = ``<expiry>.<hmac>``.
* Changing the password also changes the signing secret, which logs out every old session.
* Repeated wrong passwords trigger an escalating lockout.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

COOKIE = "nt_session"
SESSION_SECONDS = 30 * 24 * 3600
_SCRYPT = dict(n=2**15, r=8, p=1, maxmem=96 * 1024 * 1024, dklen=32)


class Auth:
    def __init__(self, path: Path, now: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._now = now
        self._fails = 0
        self._locked_until = 0.0

    # ---- storage
    def _load(self) -> dict[str, str] | None:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return None

    @property
    def configured(self) -> bool:
        return self._load() is not None

    def set_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        salt = os.urandom(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
        data = {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(),
                "secret": base64.b64encode(secrets.token_bytes(32)).decode()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        tmp.replace(self.path)
        self._fails, self._locked_until = 0, 0.0

    # ---- login
    def lockout_remaining(self) -> int:
        return max(0, int(self._locked_until - self._now() + 0.999))

    def verify_password(self, password: str) -> bool:
        """Constant-time check. Callers must check ``lockout_remaining()`` first."""
        data = self._load()
        if data is None:
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(data["salt"]), **_SCRYPT)
        ok = hmac.compare_digest(digest, base64.b64decode(data["hash"]))
        if ok:
            self._fails, self._locked_until = 0, 0.0
        else:
            self._fails += 1
            if self._fails >= 5:   # 2s, 4s, 8s ... capped at 5 minutes
                self._locked_until = self._now() + min(300, 2 ** (self._fails - 4))
        return ok

    # ---- session tokens
    def _sig(self, exp: str) -> str:
        data = self._load()
        if data is None:
            return ""
        return hmac.new(base64.b64decode(data["secret"]), exp.encode(), hashlib.sha256).hexdigest()

    def make_token(self) -> str:
        exp = str(int(self._now()) + SESSION_SECONDS)
        return f"{exp}.{self._sig(exp)}"

    def check_token(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        exp, _, sig = token.partition(".")
        if not exp.isdigit() or int(exp) < self._now():
            return False
        expected = self._sig(exp)
        return bool(expected) and hmac.compare_digest(sig, expected)
