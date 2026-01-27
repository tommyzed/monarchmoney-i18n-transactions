from cryptography.fernet import Fernet

key = Fernet.generate_key()
print(f"Your FERNET_KEY: {key.decode()}")
print("\nAdd this to your .env file or environment variables.")
