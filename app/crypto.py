from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    key = os.getenv("PAT_SECRET_KEY", "")
    if not key:
        # Derive a 32-byte key from SESSION_SECRET_KEY for dev fallback
        secret = os.getenv("SESSION_SECRET_KEY", "dev-change-me")
        key = base64.urlsafe_b64encode(secret.ljust(32)[:32].encode()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_pat(pat: str) -> str:
    return _get_fernet().encrypt(pat.encode()).decode()


def decrypt_pat(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()
