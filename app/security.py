# app/security.py
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

PBKDF2_ALGO = "sha256"
PBKDF2_ITERATIONS = 210_000
SALT_BYTES = 16
DKLEN = 32


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def hash_password(password: str) -> str:
    """
    비밀번호를 PBKDF2로 해시한다.
    저장 포맷: pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>
    """
    if not isinstance(password, str):
        raise TypeError("password must be str")
    pw = password.encode("utf-8")
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(PBKDF2_ALGO, pw, salt, PBKDF2_ITERATIONS, dklen=DKLEN)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """
    저장된 해시 문자열과 비밀번호를 검증한다.
    """
    try:
        scheme, iters_s, salt_b64, hash_b64 = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
        got = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"), salt, iters, dklen=len(expected))
        return hmac.compare_digest(got, expected)
    except Exception:
        return False
