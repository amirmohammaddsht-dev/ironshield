"""
IronShield - Cryptography Utilities
Path: ironshield/utils/crypto.py
Purpose: Encryption and decryption of sensitive data (keys, tokens, passwords)
"""

import os
import secrets
import hashlib
import base64
from typing import Optional
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ironshield.utils.logger import get_logger

logger = get_logger("crypto")

KEY_FILE = Path("/opt/ironshield/keys/.master_key")


class CryptoManager:
    """
    Manages encryption of sensitive data using Fernet (AES-128-CBC).

    Usage:
        crypto = CryptoManager()
        encrypted = crypto.encrypt("my_secret_token")
        original = crypto.decrypt(encrypted)
    """

    def __init__(self, key_file: Optional[Path] = None):
        self._key_file = key_file or KEY_FILE
        self._fernet: Optional[Fernet] = None

    def _load_or_create_key(self) -> bytes:
        """Load existing master key or generate a new one."""
        if self._key_file.exists():
            with open(self._key_file, "rb") as f:
                return f.read()

        key = Fernet.generate_key()
        self._key_file.parent.mkdir(parents=True, exist_ok=True)
        self._key_file.touch(mode=0o600)
        with open(self._key_file, "wb") as f:
            f.write(key)
        os.chmod(self._key_file, 0o600)
        logger.info("New master encryption key generated")
        return key

    def _get_fernet(self) -> Fernet:
        """Get Fernet instance (lazy initialization)."""
        if self._fernet is None:
            key = self._load_or_create_key()
            self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Args:
            plaintext: Raw text to encrypt

        Returns:
            str: Base64-encoded ciphertext
        """
        try:
            fernet = self._get_fernet()
            encrypted = fernet.encrypt(plaintext.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string.

        Args:
            ciphertext: Base64-encoded ciphertext

        Returns:
            str: Original plaintext
        """
        try:
            fernet = self._get_fernet()
            decrypted = fernet.decrypt(ciphertext.encode("utf-8"))
            return decrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise

    def encrypt_file(self, file_path: Path) -> None:
        """Encrypt a file in-place."""
        with open(file_path, "rb") as f:
            data = f.read()
        fernet = self._get_fernet()
        encrypted = fernet.encrypt(data)
        with open(file_path, "wb") as f:
            f.write(encrypted)
        logger.debug(f"File encrypted: {file_path}")

    def decrypt_file(self, file_path: Path) -> bytes:
        """Decrypt a file and return its contents."""
        with open(file_path, "rb") as f:
            data = f.read()
        fernet = self._get_fernet()
        return fernet.decrypt(data)


def generate_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Args:
        length: Token length in bytes

    Returns:
        str: Hex-encoded token
    """
    return secrets.token_hex(length)


def generate_password(length: int = 24) -> str:
    """
    Generate a secure random alphanumeric password.

    Args:
        length: Password length

    Returns:
        str: Random password
    """
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """
    Hash a password using PBKDF2-SHA256.

    Args:
        password: Raw password
        salt: Optional salt bytes (auto-generated if None)

    Returns:
        tuple: (hash_b64, salt_b64)
    """
    if salt is None:
        salt = os.urandom(32)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = kdf.derive(password.encode("utf-8"))
    return (
        base64.b64encode(key).decode("utf-8"),
        base64.b64encode(salt).decode("utf-8"),
    )


def verify_password(password: str, hash_b64: str, salt_b64: str) -> bool:
    """
    Verify a password against a stored hash.

    Args:
        password: Input password
        hash_b64: Stored hash
        salt_b64: Stored salt

    Returns:
        bool: True if password matches
    """
    salt = base64.b64decode(salt_b64)
    expected_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(expected_hash, hash_b64)


def file_checksum(file_path: Path) -> str:
    """
    Compute SHA256 checksum of a file.

    Args:
        file_path: Path to file

    Returns:
        str: Hex checksum
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


_default_crypto: Optional[CryptoManager] = None


def get_crypto() -> CryptoManager:
    """Get the default CryptoManager singleton."""
    global _default_crypto
    if _default_crypto is None:
        _default_crypto = CryptoManager()
    return _default_crypto
