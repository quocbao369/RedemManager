"""LDPlayer 14 (second installation) support.

Covers three layers:
- config: LDPlayer14Config derives ldconsole/adb from the install folder
- database: old (ld_index UNIQUE) schema migrates to (ld_version, ld_index)
  without nulling account_mapping rows
- service: ldconsole commands route to the installation the caller asked
  for; LD14 raises before any shell-out when it was never configured
"""
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from config import settings
from database import Database
from services.ldplayer_service import LDPlayerService, InstanceInfo


@pytest.fixture
def client():
    import app as app_module
    return TestClient(app_module.app)


def _ok(stdout="", stderr="", returncode=0):
    return type("R", (), {"stdout": stdout, "stderr": stderr,
                          "returncode": returncode})()


# ------------------------------------------------------------------ config
def test_ld14_config_unset_by_default():
    cfg = settings.ldplayer14
    # The test environment never configures LDPLAYER14_PATH.
    assert cfg.ldconsole_path is None
    assert cfg.is_configured() is False


def test_ld14_config_derives_executables_from_folder(tmp_path):
    try:
        old_console = settings.ldplayer14.ldconsole_path
        settings.ldplayer14.ldconsole_path = tmp_path / "ldconsole.exe"
        assert settings.ldplayer14.is_configured() is False  # file missing

        (tmp_path / "ldconsole.exe").touch()
        assert settings.ldplayer14.is_configured() is True
    finally:
        settings.ldplayer14.ldconsole_path = old_console


def test_apply_settings_update_enables_ld14(tmp_path):
    from config import apply_settings_update, LDPlayer14Config

    old = (settings.ldplayer14.ldplayer_path, settings.ldplayer14.ldconsole_path,
           settings.ldplayer14.adb_path)
    try:
        folder = tmp_path / "LDPlayer14"
        folder.mkdir()
        apply_settings_update({"ldplayer14_path": str(folder)})
        assert settings.ldplayer14.ldplayer_path == folder
        assert settings.ldplayer14.ldconsole_path == folder / "ldconsole.exe"
        assert settings.ldplayer14.adb_path == folder / "adb.exe"

        # An empty string disables the installation again.
        apply_settings_update({"ldplayer14_path": ""})
        assert settings.ldplayer14.ldplayer_path is None
        assert settings.ldplayer14.ldconsole_path is None
        assert settings.ldplayer14.adb_path is None
        assert isinstance(settings.ldplayer14, LDPlayer14Config)
    finally:
        settings.ldplayer14.ldplayer_path, old_path = old[0], None
        settings.ldplayer14.ldconsole_path = old[1]
        settings.ldplayer14.adb_path = old[2]


# --------------------------------------------------------------- migration
OLD_SCHEMA = """
CREATE TABLE instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ld_index INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'stopped',
    serial TEXT,
    android_version TEXT,
    cpu_cores INTEGER,
    memory_mb INTEGER,
    resolution TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE account_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_email TEXT UNIQUE NOT NULL,
    instance_id INTEGER,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE SET NULL
);
"""


def _build_old_db(db_file: Path) -> None:
    with sqlite3.connect(db_file) as conn:
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO instances (ld_index, name, status) VALUES (3, 'keep', 'stopped')")
        conn.execute("INSERT INTO account_mapping (account_email, instance_id) VALUES ('a@x.com', 1)")
        conn.commit()


def test_migration_adds_ld_version_and_keeps_children(tmp_path):
    db_file = tmp_path / "old_schema.db"
    _build_old_db(db_file)

    original = settings.database.path
    try:
        settings.database.path = db_file
        Database.reopen()
        Database.initialize()

        with Database.transaction() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(instances)").fetchall()}
            assert "ld_version" in cols

            row = conn.execute(
                "SELECT ld_version, ld_index, name, serial FROM instances").fetchone()
            assert row["ld_version"] == 9, "existing rows become version 9"
            assert row["ld_index"] == 3 and row["name"] == "keep"

            # ON DELETE SET NULL children must survive the table rebuild.
            mapping = conn.execute(
                "SELECT instance_id FROM account_mapping WHERE account_email = 'a@x.com'"
            ).fetchone()
            assert mapping["instance_id"] == 1

            # Same index is now legal on both installations...
            conn.execute(
                "INSERT INTO instances (ld_index, ld_version, name) VALUES (3, 14, 'ld14')")
            # ...but never twice on the same one.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO instances (ld_index, ld_version, name) VALUES (3, 9, 'dup')")
    finally:
        settings.database.path = original
        Database.reopen()
        Database.initialize()


# ----------------------------------------------------------------- routing
class RecordingConsole:
    """Stand-in for _run_cmd: records (command, ld_version) per call."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=None, ld_version=9):
        self.calls.append((args[0] if args else None, ld_version))
        if args and args[0] == "list2":
            return _ok("0,LDPlayer-0,,,0,0,0,480,854,240\n")
        if args and args[0] == "add":
            return _ok("index: 1")
        return _ok()


def test_console_path_raises_when_ld14_not_configured():
    svc = LDPlayerService()
    with pytest.raises(RuntimeError, match="LDPlayer 14"):
        svc._console_path(14)


def test_create_instance_routes_every_call_to_ld14():
    svc = LDPlayerService()
    rec = RecordingConsole()
    with patch.object(svc, "_run_cmd", side_effect=rec), \
            patch.object(svc, "_finalize_new_instance", return_value=7) as fin:
        db_id = svc.create_instance("user@gmail.com", ld_version=14)

    assert db_id == 7
    versions = {v for _, v in rec.calls}
    assert versions == {14}, f"all calls must target LD14, got {rec.calls}"
    # "add" (create) ran through the LD14 console; "modify" belongs to the
    # patched-out _finalize_new_instance, so only create-time calls show up.
    commands = [c for c, _ in rec.calls]
    assert "add" in commands
    fin.assert_called_once_with(1, True, 14)


def test_create_instance_defaults_to_ld9():
    svc = LDPlayerService()
    rec = RecordingConsole()
    with patch.object(svc, "_run_cmd", side_effect=rec), \
            patch.object(svc, "_finalize_new_instance", return_value=1):
        svc.create_instance("user@gmail.com")
    assert {v for _, v in rec.calls} == {9}


def test_sync_stores_both_installations(tmp_path):
    svc = LDPlayerService()
    rec = RecordingConsole()
    old_console = settings.ldplayer14.ldconsole_path
    fake_console = tmp_path / "ldconsole.exe"
    fake_console.touch()
    try:
        settings.ldplayer14.ldconsole_path = fake_console
        with patch.object(svc, "_run_cmd", side_effect=rec):
            svc.sync_instances_to_db()
    finally:
        settings.ldplayer14.ldconsole_path = old_console

    with Database.transaction() as conn:
        rows = conn.execute(
            "SELECT ld_version, ld_index, name FROM instances").fetchall()
        keys = {(r["ld_version"], r["ld_index"]) for r in rows}
    # list2 answers the same for both installs, so both keys must exist.
    assert (9, 0) in keys and (14, 0) in keys
    # Clean up so other tests never see these rows.
    with Database.transaction() as conn:
        conn.execute("DELETE FROM instances WHERE name = 'LDPlayer-0'")


# --------------------------------------------------------------------- api
def test_runtimes_endpoint_lists_both_versions(client):
    r = client.get("/api/ldplayer/runtimes")
    assert r.status_code == 200
    versions = {v["version"] for v in r.json()["versions"]}
    assert versions == {9, 14}
    ld14 = [v for v in r.json()["versions"] if v["version"] == 14][0]
    assert ld14["available"] is False  # never configured in the test env


def test_create_instance_rejects_unconfigured_ld14(client):
    r = client.post("/api/instances", json={"name": "x", "ld_version": 14})
    assert r.status_code == 400
    assert "LDPlayer 14" in r.json()["detail"]


def test_create_instance_rejects_unknown_version(client):
    r = client.post("/api/instances", json={"name": "x", "ld_version": 11})
    assert r.status_code == 400


def test_api_instances_merges_both_installations(client, tmp_path):
    old_console = settings.ldplayer14.ldconsole_path
    fake_console = tmp_path / "ldconsole.exe"
    fake_console.touch()
    inst9 = [InstanceInfo(index=0, name="A", status="stopped", ld_version=9)]
    inst14 = [InstanceInfo(index=0, name="B", status="stopped", ld_version=14)]
    try:
        settings.ldplayer14.ldconsole_path = fake_console
        with patch("app.ldplayer_service.list_instances",
                   side_effect=[inst9, inst14]), \
                patch("app.ldplayer_service.get_db_instances", return_value=[
                    {"id": 1, "ld_index": 0, "ld_version": 9},
                    {"id": 2, "ld_index": 0, "ld_version": 14},
                ]):
            r = client.get("/api/instances")
    finally:
        settings.ldplayer14.ldconsole_path = old_console

    assert r.status_code == 200
    body = r.json()
    assert [i["ld_version"] for i in body] == [9, 14]
    # The same index resolves to DIFFERENT db rows per installation.
    assert body[0]["id"] == 1 and body[1]["id"] == 2
    assert body[1]["name"] == "B"
