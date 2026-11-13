import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from config import ENV_FILE, apply_settings_update, settings


@pytest.fixture
def client():
    import app as app_module
    return TestClient(app_module.app)


@pytest.fixture
def restore_config():
    """Put the real config and .env back after a test touched them."""
    before = {
        "ldplayer_path": settings.ldplayer.ldplayer_path,
        "ldconsole_path": settings.ldplayer.ldconsole_path,
        "adb_path": settings.ldplayer.adb_path,
        "bing_apk_path": settings.apps.bing_apk_path,
        "oxyproxy_xapk_path": settings.apps.oxyproxy_xapk_path,
        "default_timeout": settings.ldplayer.default_timeout,
        "boot_timeout": settings.ldplayer.boot_timeout,
        "server_port": settings.server.port,
    }
    env_before = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else None
    env_snapshot = {k: os.environ.get(k) for k in
                    ("LDPLAYER_PATH", "ADB_PATH", "BING_APK_PATH",
                     "OXYPROXY_XAPK_PATH", "DEFAULT_TIMEOUT",
                     "BOOT_TIMEOUT", "SERVER_PORT")}
    yield
    settings.ldplayer.ldplayer_path = before["ldplayer_path"]
    settings.ldplayer.ldconsole_path = before["ldconsole_path"]
    settings.ldplayer.adb_path = before["adb_path"]
    settings.ldplayer.default_timeout = before["default_timeout"]
    settings.ldplayer.boot_timeout = before["boot_timeout"]
    settings.apps.bing_apk_path = before["bing_apk_path"]
    settings.apps.oxyproxy_xapk_path = before["oxyproxy_xapk_path"]
    settings.server.port = before["server_port"]
    if env_before is None:
        ENV_FILE.unlink(missing_ok=True)
    else:
        ENV_FILE.write_text(env_before, encoding="utf-8")
    for key, value in env_snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_saved_apk_path_is_returned_after_save(client, restore_config):
    """Regression: the Settings form kept reverting the APK path.

    POST only wrote to os.environ, while `settings` is built once at import
    time, so GET returned the old path and the form reloaded it immediately.
    """
    new_path = r"D:\AccMicro\demo\NEW_BING.apk"
    r = client.post("/api/settings", json={"bing_apk_path": new_path})
    assert r.status_code == 200
    assert r.json()["changes"]["bing_apk_path"] == new_path

    back = client.get("/api/settings").json()
    assert back["bing_apk_path"] == new_path, (
        "GET must echo what was just saved, not the import-time value")


def test_saved_path_is_used_by_installer(client, restore_config):
    from services.installer_service import installer_service

    new_path = r"D:\AccMicro\demo\NEW_BING.apk"
    client.post("/api/settings", json={"bing_apk_path": new_path})
    # The installer used to cache the import-time path in __init__.
    assert str(installer_service.bing_apk) == new_path


def test_saved_path_is_used_by_ldplayer_and_adb(client, restore_config):
    from services.adb_service import adb_service
    from services.ldplayer_service import ldplayer_service

    folder = r"C:\ProgramFiles\LDPlayer"
    client.post("/api/settings", json={
        "ldplayer_path": folder,
        "adb_path": folder + r"\tools\adb.exe",
    })
    assert str(ldplayer_service.ldconsole) == folder + r"\ldconsole.exe"
    assert str(ldplayer_service.adb) == folder + r"\tools\adb.exe"
    assert str(adb_service.adb_path) == folder + r"\tools\adb.exe"


def test_saved_path_survives_a_restart(client, restore_config):
    """The value has to land in .env, otherwise a restart drops it."""
    new_path = r"D:\AccMicro\demo\NEW_BING.apk"
    client.post("/api/settings", json={"bing_apk_path": new_path})

    assert ENV_FILE.exists()
    body = ENV_FILE.read_text(encoding="utf-8")
    assert f"BING_APK_PATH={new_path}" in body
    # Unrelated keys and comments must be preserved.
    assert "# LDPlayer Configuration" in body
    assert "WORKFLOW_LOCK_TIMEOUT" in body


def test_missing_file_is_reported_but_still_saved(client, restore_config):
    """A path that does not exist yet must still be kept, just flagged."""
    missing = r"D:\AccMicro\demo\DOES_NOT_EXIST.apk"
    r = client.post("/api/settings", json={"bing_apk_path": missing})

    assert r.status_code == 200
    assert r.json()["success"] is False
    assert any("Bing APK not found" in e for e in r.json()["errors"])
    assert client.get("/api/settings").json()["bing_apk_path"] == missing


def test_apply_updates_ldconsole_next_to_ldplayer(restore_config):
    apply_settings_update({"ldplayer_path": r"D:\other\LD"})
    assert str(settings.ldplayer.ldconsole_path) == r"D:\other\LD\ldconsole.exe"
    # adb follows the folder unless overridden explicitly.
    assert str(settings.ldplayer.adb_path) == r"D:\other\LD\adb.exe"