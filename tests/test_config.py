import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from config import settings, validate_paths


def test_settings_load():
    assert settings.ldplayer.ldplayer_path is not None
    assert settings.ldplayer.ldconsole_path is not None
    assert settings.ldplayer.adb_path is not None
    assert settings.apps.bing_apk_path is not None
    assert settings.apps.oxyproxy_xapk_path is not None
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8765


def test_ldplayer_config_defaults():
    assert settings.ldplayer.default_timeout == 120
    assert settings.ldplayer.boot_timeout == 300
    assert settings.ldplayer.adb_connect_timeout == 60


def test_app_config_defaults():
    assert settings.apps.bing_package_name == "com.microsoft.bing"
    assert settings.apps.oxyproxy_package_name == "com.oxyproxy.app"


def test_server_config():
    assert settings.server.reload is False


def test_database_config():
    # conftest.py redirects the DB to an isolated temp file, so only assert the
    # suffix here - never the production path.
    assert settings.database.path.name.endswith(".db")
    assert settings.database.echo is False


def test_logging_config():
    assert settings.logging.level == "INFO"
    assert settings.logging.file.name == "redem.log"


def test_validate_paths_missing(monkeypatch, tmp_path):
    # Point the live config at paths that do not exist. Patching the object
    # rather than reloading the module: reload() builds a brand new `settings`,
    # which every module that did `from config import settings` would keep
    # pointing away from, silently breaking later tests.
    folder = tmp_path / "nonexistent"
    monkeypatch.setattr(settings.ldplayer, "ldplayer_path", folder)
    monkeypatch.setattr(settings.ldplayer, "ldconsole_path", folder / "ldconsole.exe")
    monkeypatch.setattr(settings.ldplayer, "adb_path", folder / "adb.exe")
    monkeypatch.setattr(settings.apps, "bing_apk_path", tmp_path / "nonexistent.apk")
    monkeypatch.setattr(settings.apps, "oxyproxy_xapk_path", tmp_path / "nonexistent.xapk")

    errors = validate_paths()
    assert len(errors) == 4
    assert any("ldconsole" in e for e in errors)
    assert any("adb" in e for e in errors)
    assert any("Bing APK" in e for e in errors)
    assert any("OxyProxy" in e for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])