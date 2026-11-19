"""Parola hash'i, oturum token'lari ve giris deneme siniri.

- Parola: stdlib scrypt (ek bagimlilik yok), kullanici basina rastgele tuz.
- Oturum: cerezde 32 baytlik rastgele token; DB'de yalnizca sha256'si
  saklanir (DB sizsa bile cerez taklit edilemez).
- CSRF: oturuma bagli ayri bir token; durum degistiren istekler bunu
  X-CSRF-Token basliginda gondermek zorunda.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
MIN_PASSWORD_LENGTH = 12


class WeakPasswordError(ValueError):
    pass


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"parola en az {MIN_PASSWORD_LENGTH} karakter olmali")
    if password.strip() != password:
        raise WeakPasswordError("parola bosluk ile baslayip bitemez")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# Kullanici yoksa da ayni sure harcansin (kullanici adi tahmini zamanlamadan anlasilmasin).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_password_or_dummy(password: str, encoded: str | None) -> bool:
    if encoded is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, encoded)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginThrottle:
    """(kullanici, ip) basina: pencere icinde `max_failures` hatali deneme ->
    `lockout_seconds` kilit. Bellek ici (tek surec, yerel panel icin yeterli)."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300, lockout_seconds: int = 300, clock=time.monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.clock = clock
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._locked_until: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, ip: str) -> tuple[str, str]:
        return username.strip().lower(), ip

    def retry_after(self, username: str, ip: str) -> int:
        """Kilitliyse kalan saniye, degilse 0."""
        with self._lock:
            until = self._locked_until.get(self._key(username, ip), 0.0)
            remaining = until - self.clock()
            return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, username: str, ip: str) -> None:
        key = self._key(username, ip)
        now = self.clock()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds] + [now]
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_seconds
                self._failures[key] = []

    def record_success(self, username: str, ip: str) -> None:
        key = self._key(username, ip)
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)
