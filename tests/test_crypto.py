import os
import pytest
from cryptography.fernet import Fernet
from bridge_app.utils.crypto import get_key, encrypt, decrypt

def test_get_key_raises_value_error_when_missing(monkeypatch):
    # Ensure FERNET_KEY is not in env
    monkeypatch.delenv("FERNET_KEY", raising=False)
    with pytest.raises(ValueError) as exc_info:
        get_key()
    assert "FERNET_KEY environment variable is not set" in str(exc_info.value)

def test_get_key_returns_bytes_when_string_key_provided(monkeypatch):
    # A valid Fernet key in str format
    valid_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", valid_key)
    key = get_key()
    assert isinstance(key, bytes)
    assert key == valid_key.encode()

def test_get_key_returns_bytes_when_bytes_key_provided(monkeypatch):
    # A valid Fernet key in bytes format
    valid_key_bytes = Fernet.generate_key()
    # Set as bytes in monkeypatch
    monkeypatch.setenv("FERNET_KEY", valid_key_bytes.decode())
    # Note that os.environ actually stores everything as str. Let's mock os.getenv to return bytes directly.
    monkeypatch.setattr(os, "getenv", lambda name: valid_key_bytes)
    key = get_key()
    assert isinstance(key, bytes)
    assert key == valid_key_bytes

def test_encrypt_and_decrypt_cycle(monkeypatch):
    valid_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", valid_key)

    original_text = "secret message 123"
    encrypted_data = encrypt(original_text)

    assert isinstance(encrypted_data, bytes)
    assert encrypted_data != original_text.encode()

    decrypted_text = decrypt(encrypted_data)
    assert decrypted_text == original_text
