"""
Pytest configuration and shared fixtures for IronShield tests.
"""

import pytest
from pathlib import Path


@pytest.fixture(scope="session")
def tmp_base(tmp_path_factory):
    """Shared temporary directory for the test session."""
    return tmp_path_factory.mktemp("ironshield_test")
