import os
from cryptography.fernet import Fernet

def get_key() -> bytes:
    key = os.getenv("FERNET_KEY")
    if not key or (isinstance(key, str) and not key.strip()):
        raise ValueError("FERNET_KEY environment variable is not set")
    return key.strip().encode() if isinstance(key, str) else key

def encrypt(data: str) -> bytes:
    f = Fernet(get_key())
    return f.encrypt(data.encode())

def decrypt(token: bytes) -> str:
    f = Fernet(get_key())
    return f.decrypt(token).decode()
