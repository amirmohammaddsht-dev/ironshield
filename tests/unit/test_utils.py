"""
Tests for IronShield utility modules.
"""

import pytest
from ironshield.utils.validators import (
    is_valid_ip, is_valid_port, is_valid_cidr,
    is_valid_telegram_token, is_valid_domain,
    is_valid_username, validate_traffic_gb, validate_days,
)
from ironshield.utils.crypto import generate_token, generate_password, hash_password, verify_password


class TestValidators:

    def test_valid_ipv4(self):
        assert is_valid_ip("192.168.1.1") is True

    def test_valid_ipv6(self):
        assert is_valid_ip("::1") is True

    def test_invalid_ip(self):
        assert is_valid_ip("999.999.999.999") is False
        assert is_valid_ip("not-an-ip") is False

    def test_valid_port(self):
        assert is_valid_port(443) is True
        assert is_valid_port(1) is True
        assert is_valid_port(65535) is True

    def test_invalid_port(self):
        assert is_valid_port(0) is False
        assert is_valid_port(65536) is False

    def test_valid_cidr(self):
        assert is_valid_cidr("10.8.0.0/24") is True
        assert is_valid_cidr("192.168.0.0/16") is True

    def test_invalid_cidr(self):
        assert is_valid_cidr("10.8.0.0/33") is False

    def test_valid_telegram_token(self):
        assert is_valid_telegram_token("123456789:JA7ZVeeDkqvqoIVqL-hU39-1xmVGC9LE7Gk") is True

    def test_invalid_telegram_token(self):
        assert is_valid_telegram_token("not-a-token") is False

    def test_valid_domain(self):
        assert is_valid_domain("example.com") is True
        assert is_valid_domain("v.example.com") is True

    def test_invalid_domain(self):
        assert is_valid_domain("not_a_domain") is False

    def test_valid_username(self):
        assert is_valid_username("ali_user") is True
        assert is_valid_username("testuser123") is True

    def test_invalid_username(self):
        assert is_valid_username("ab") is False
        assert is_valid_username("1startswithdigit") is False

    def test_validate_traffic_gb(self):
        assert validate_traffic_gb("50") == 50.0
        assert validate_traffic_gb("unlimited") is None
        assert validate_traffic_gb("0") is None

    def test_validate_days(self):
        assert validate_days("30") == 30
        assert validate_days("0") is None
        assert validate_days("abc") is None


class TestCrypto:

    def test_generate_token(self):
        token = generate_token()
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_generate_password(self):
        pwd = generate_password(24)
        assert len(pwd) == 24

    def test_tokens_are_unique(self):
        tokens = {generate_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_hash_and_verify_password(self):
        password = "SecurePassword123"
        hash_b64, salt_b64 = hash_password(password)
        assert verify_password(password, hash_b64, salt_b64) is True
        assert verify_password("WrongPassword", hash_b64, salt_b64) is False

    def test_different_salts_produce_different_hashes(self):
        password = "SamePassword"
        hash1, salt1 = hash_password(password)
        hash2, salt2 = hash_password(password)
        assert hash1 != hash2
        assert salt1 != salt2
