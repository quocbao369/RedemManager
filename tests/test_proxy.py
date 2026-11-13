import sys
import os
import socket
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from services.proxy_service import ProxyService
from database import Database


@pytest.fixture(autouse=True)
def cleanup_db():
    yield
    with Database.transaction() as conn:
        conn.execute("DELETE FROM proxy_profiles")


def test_create_proxy_profile():
    service = ProxyService()
    profile = service.create_profile(
        name="Test Proxy",
        host="192.168.1.100",
        port=8080,
        username="user",
        password="pass123",
        protocol="http"
    )

    assert profile.id is not None
    assert profile.name == "Test Proxy"
    assert profile.host == "192.168.1.100"
    assert profile.port == 8080
    assert profile.username == "user"
    assert profile.password == "pass123"
    assert profile.protocol == "http"
    assert profile.is_active is True


def test_create_proxy_no_auth():
    service = ProxyService()
    profile = service.create_profile(
        name="No Auth Proxy",
        host="10.0.0.1",
        port=3128,
    )

    assert profile.username is None
    assert profile.password is None


def test_get_profile():
    service = ProxyService()
    created = service.create_profile("Get Test", "1.2.3.4", 8080)
    retrieved = service.get_profile(created.id)

    assert retrieved is not None
    assert retrieved.id == created.id
    assert retrieved.name == "Get Test"


def test_get_profile_by_name():
    service = ProxyService()
    service.create_profile("By Name", "5.6.7.8", 9090)
    retrieved = service.get_profile_by_name("By Name")

    assert retrieved is not None
    assert retrieved.name == "By Name"


def test_list_profiles():
    service = ProxyService()
    service.create_profile("Proxy 1", "1.1.1.1", 8080)
    service.create_profile("Proxy 2", "2.2.2.2", 8080)

    profiles = service.list_profiles()
    assert len(profiles) >= 2


def test_update_profile():
    service = ProxyService()
    created = service.create_profile("Update Test", "1.1.1.1", 8080)
    updated = service.update_profile(created.id, host="2.2.2.2", port=9090)

    assert updated.host == "2.2.2.2"
    assert updated.port == 9090


def test_delete_profile():
    service = ProxyService()
    created = service.create_profile("Delete Test", "1.1.1.1", 8080)
    result = service.delete_profile(created.id)

    assert result is True
    assert service.get_profile(created.id) is None


def test_validate_host_port_valid():
    service = ProxyService()
    assert service._validate_host_port("192.168.1.1", 8080) is True
    with patch.object(socket, 'gethostbyname', return_value="1.2.3.4"):
        assert service._validate_host_port("proxy.example.com", 3128) is True
    assert service._validate_host_port("10.0.0.1", 1) is True
    assert service._validate_host_port("10.0.0.1", 65535) is True


def test_validate_host_port_invalid():
    service = ProxyService()
    assert service._validate_host_port("", 8080) is False
    assert service._validate_host_port("192.168.1.1", 0) is False
    assert service._validate_host_port("192.168.1.1", 65536) is False
    assert service._validate_host_port("192.168.1.1", -1) is False
    assert service._validate_host_port("not.a.valid.host.name.that.does.not.exist.invalid", 8080) is False


def test_encrypt_decrypt_password():
    service = ProxyService()
    encrypted = service._encrypt_password("secret123")
    decrypted = service._decrypt_password(encrypted)

    assert encrypted != "secret123"
    assert decrypted == "secret123"

    assert service._encrypt_password(None) is None
    assert service._decrypt_password(None) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])