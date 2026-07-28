import pytest
import os
from cryptography.fernet import Fernet, InvalidToken
from bridge_app.utils.crypto import get_key, encrypt, decrypt

def test_get_key_missing(monkeypatch):
    monkeypatch.delenv("FERNET_KEY", raising=False)
    with pytest.raises(ValueError, match="FERNET_KEY environment variable is not set"):
        get_key()

def test_get_key_string(monkeypatch):
    test_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", test_key)
    assert get_key() == test_key.encode()

def test_get_key_bytes(monkeypatch):
    test_key = Fernet.generate_key()
    monkeypatch.setenv("FERNET_KEY", test_key.decode())  # os.environ values are typically strings, but we can set it and test get_key typing
    # Let's monkeypatch os.getenv to return bytes directly to test that branch of isinstance(key, str)
    monkeypatch.setattr(os, "getenv", lambda name, default=None: test_key if name == "FERNET_KEY" else default)
    assert get_key() == test_key

def test_encrypt_decrypt_success(monkeypatch):
    test_key = Fernet.generate_key()
    monkeypatch.setenv("FERNET_KEY", test_key.decode())

    original_text = "secret message 123"
    token = encrypt(original_text)
    assert isinstance(token, bytes)

    decrypted_text = decrypt(token)
    assert decrypted_text == original_text

def test_decrypt_invalid_token(monkeypatch):
    test_key = Fernet.generate_key()
    monkeypatch.setenv("FERNET_KEY", test_key.decode())

    with pytest.raises(InvalidToken):
        decrypt(b"invalidtoken")
