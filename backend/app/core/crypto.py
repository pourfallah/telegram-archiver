"""Cryptography primitives.

- Fernet (AES-128-CBC + HMAC) for Telegram session strings and api_hashes at rest.
- Argon2id for dashboard password hashing.
- JWT (HS256) for dashboard access tokens.
"""
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_password_hasher = PasswordHasher()


def get_fernet() -> Fernet:
    """Return the Fernet cipher bound to SESSION_ENCRYPTION_KEY."""
    key = get_settings().session_encryption_key
    if not key:
        raise RuntimeError(
            "SESSION_ENCRYPTION_KEY is not set. Generate one with: "
            "python3 -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" "
            "and set it in the environment. Losing this key makes stored "
            "Telegram sessions and api_hashes unrecoverable."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "SESSION_ENCRYPTION_KEY is not a valid Fernet key (must be a "
            "URL-safe base64 32-byte value). Generate a fresh one with the "
            "command documented in .env.example."
        ) from exc


def encrypt_text(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_text(token: str) -> str:
    try:
        return get_fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt value — wrong SESSION_ENCRYPTION_KEY or corrupt data."
        ) from exc


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    """Return the user id encoded in a token, or raise ValueError."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired token") from exc
    sub = payload.get("sub")
    if sub is None:
        raise ValueError("Token is missing subject")
    try:
        return int(sub)
    except (TypeError, ValueError) as exc:
        raise ValueError("Token subject is not a user id") from exc
