import pytest
from updater import is_newer_version, parse_version_tuple

def test_parse_version_tuple():
    assert parse_version_tuple("1.9.4") == (1, 9, 4)
    assert parse_version_tuple("v1.9.10") == (1, 9, 10)
    assert parse_version_tuple("2.0.0") == (2, 0, 0)

def test_is_newer_version():
    assert is_newer_version(current="1.9.4", remote="1.9.5") is True
    assert is_newer_version(current="1.9.4", remote="v1.9.10") is True
    assert is_newer_version(current="1.9.4", remote="2.0.0") is True
    assert is_newer_version(current="1.9.4", remote="1.9.4") is False
    assert is_newer_version(current="1.9.4", remote="1.9.3") is False
