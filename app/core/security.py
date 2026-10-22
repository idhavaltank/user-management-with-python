"""Password and opaque-token cryptographic helpers."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id is centralized here so password policy changes are not scattered
# across routers or services.
password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password with Argon2id; callers must never persist raw passwords."""

    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether a password matches a stored Argon2 hash."""

    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def generate_token() -> str:
    """Generate a high-entropy value for a session or CSRF token."""

    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Hash an opaque token before it is used as a database lookup key."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
