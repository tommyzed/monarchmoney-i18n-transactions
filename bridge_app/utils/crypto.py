import os
from cryptography.fernet import Fernet

def get_key() -> bytes:
    key = os.getenv("FERNET_KEY")
    if not key:
        raise ValueError("FERNET_KEY environment variable is not set")
    return key.encode() if isinstance(key, str) else key

def encrypt(data: str) -> bytes:
    f = Fernet(get_key())
    return f.encrypt(data.encode())

def decrypt(token: bytes) -> str:
    f = Fernet(get_key())
    return f.decrypt(token).decode()
