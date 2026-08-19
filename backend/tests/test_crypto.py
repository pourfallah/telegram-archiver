"""Cryptography unit tests: Fernet, Argon2, JWT."""
import datetime

import jwt
import pytest

from app.config import Settings
from app.core import crypto


def _key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_fernet_roundtrip(monkeypatch):
    key = _key()
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(session_encryption_key=key))
    token = crypto.encrypt_text("hello-世界")
    assert token != "hello-世界"
    assert crypto.decrypt_text(token) == "hello-世界"


def test_fernet_missing_key_raises(monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(session_encryption_key=""))
    with pytest.raises(RuntimeError, match="SESSION_ENCRYPTION_KEY"):
        crypto.encrypt_text("x")


def test_fernet_invalid_key_raises(monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(session_encryption_key="not-a-key"))
    with pytest.raises(RuntimeError, match="valid Fernet key"):
        crypto.encrypt_text("x")


def test_fernet_wrong_key_fails_to_decrypt(monkeypatch):
    key1, key2 = _key(), _key()
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(session_encryption_key=key1))
    token = crypto.encrypt_text("secret")
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(session_encryption_key=key2))
    with pytest.raises(ValueError):
        crypto.decrypt_text(token)


def test_password_hash_verify():
    h = crypto.hash_password("hunter2")
    assert h != "hunter2"
    assert crypto.verify_password("hunter2", h)
    assert not crypto.verify_password("wrong", h)
    assert not crypto.verify_password("hunter2", "not-a-hash")


def test_jwt_roundtrip(monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(jwt_secret="s3cret"))
    token = crypto.create_access_token(7)
    assert crypto.decode_access_token(token) == 7


def test_jwt_expired_rejected(monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(jwt_secret="s3cret"))
    token = crypto.create_access_token(7, expires_minutes=-1)
    with pytest.raises(ValueError, match="expired"):
        crypto.decode_access_token(token)


def test_jwt_tampered_rejected(monkeypatch):
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(jwt_secret="s3cret"))
    token = crypto.create_access_token(7)
    with pytest.raises(ValueError):
        crypto.decode_access_token(token + "x")


def test_jwt_foreign_signature_rejected():
    foreign = jwt.encode({"sub": "1"}, "other-secret", algorithm="HS256")
    with pytest.raises(ValueError):
        crypto.decode_access_token(foreign)


def test_login_at_helper_type():
    # datetime.now(timezone.utc) used by session manager — sanity check
    assert datetime.datetime.now(datetime.UTC).tzinfo is not None
