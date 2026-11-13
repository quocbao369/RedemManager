import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from config import ENV_FILE, settings
from database import Database
from services.rewards_service import rewards_service


@pytest.fixture
def client():
    import app as app_module
    return TestClient(app_module.app)


def _build_rewards_db(db_file: Path, n_accounts: int = 3) -> None:
    """Minimal rewards_data.db: the tables `accounts` / `account_status`
    that rewards_service joins, with n accounts."""
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE accounts (
                email TEXT PRIMARY KEY,
                password TEXT,
                totp_secret TEXT,
                recovery_email TEXT,
                geo_locale TEXT,
                lang_code TEXT,
                proxy TEXT,
                tag TEXT,
                account_group TEXT,
                created_at TEXT,
                couponDiscount REAL,
                points REAL,
                suspended INTEGER,
                initial_points REAL,
                collected_points REAL,
                `rank` TEXT
            )""")
        conn.execute("""
            CREATE TABLE account_status (
                email TEXT PRIMARY KEY,
                points REAL,
                suspended INTEGER,
                `rank` TEXT,
                initial_points REAL,
                collected_points REAL
            )""")
        for i in range(n_accounts):
            conn.execute(
                "INSERT INTO accounts (email, password, created_at, couponDiscount, tag) "
                "VALUES (?, ?, '2026-11-07', 50, 'Guest')",
                (f"user{i}@example.com", f"pw{i}"),
            )
        conn.commit()


@pytest.fixture
def restore_db_config():
    """Put the original DB paths (config + .env + services) back."""
    before_env = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else None
    env_keys = ("DATABASE_PATH", "REWARDS_DB_PATH")
    env_snapshot = {k: os.environ.get(k) for k in env_keys}
    before = {
        "database_path": settings.database.path,
        "rewards_db_path": settings.rewards.db_path,
        "service_db_path": rewards_service.db_path,
    }
    yield
    # Settings saved during the test must stop being served.
    settings.database.path = before["database_path"]
    settings.rewards.db_path = before["rewards_db_path"]
    rewards_service.db_path = before["service_db_path"]
    Database.reopen()
    Database.initialize()
    if before_env is None:
        ENV_FILE.unlink(missing_ok=True)
    else:
        ENV_FILE.write_text(before_env, encoding="utf-8")
    for key, value in env_snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_load_repoints_rewards_service_and_counts(client, restore_db_config):
    scratch = Path(tempfile.mkdtemp(prefix="redem_load_test_"))
    new_db = scratch / "rewards2.db"
    _build_rewards_db(new_db)

    r = client.post("/api/settings/load", json={"rewards_db_path": str(new_db)})
    body = r.json()
    assert r.status_code == 200
    assert body["rewards_db"]["changed"] is True
    assert body["rewards_db"]["accounts"] == 3
    assert body["rewards_db"]["path"] == str(new_db)
    assert rewards_service.db_path == new_db, (
        "services must read the NEW file immediately, not after a restart")


def test_load_swaps_app_database_without_restart(client, restore_db_config):
    scratch = Path(tempfile.mkdtemp(prefix="redem_load_test_"))
    app_db = scratch / "sub" / "app2.db"

    r = client.post("/api/settings/load", json={"database_path": str(app_db)})
    body = r.json()
    assert r.status_code == 200
    assert body["app_db"]["changed"] is True
    assert body["app_db"]["path"] == str(app_db)
    # Schema was created in the new file; no instances yet.
    assert body["app_db"]["instances"] == 0
    assert app_db.exists()
    # Subsequent reads keep using the new file.
    with Database.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM instances").fetchone()["n"]
    assert count == 0


def test_load_reports_missing_rewards_file(client, restore_db_config):
    missing = Path(tempfile.mkdtemp(prefix="redem_load_test_")) / "no_such.db"
    r = client.post("/api/settings/load", json={"rewards_db_path": str(missing)})
    body = r.json()
    assert r.status_code == 200
    assert body["success"] is False
    assert any("khong ton tai" in e for e in body["errors"])
    assert body["rewards_db"]["accounts"] is None


def test_load_of_non_rewards_file_is_flagged(client, restore_db_config, tmp_path):
    bogus = tmp_path / "bogus.db"
    with sqlite3.connect(bogus) as conn:
        conn.execute("CREATE TABLE something_else (id INTEGER)")

    r = client.post("/api/settings/load", json={"rewards_db_path": str(bogus)})
    body = r.json()
    assert r.status_code == 200
    assert any("khong doc duoc" in e for e in body["errors"])
    assert body["rewards_db"]["accounts"] is None


def test_load_persists_paths_to_env(client, restore_db_config):
    scratch = Path(tempfile.mkdtemp(prefix="redem_load_test_"))
    rewards_db = scratch / "rewards3.db"
    _build_rewards_db(rewards_db)

    client.post("/api/settings/load", json={
        "rewards_db_path": str(rewards_db),
    })
    assert ENV_FILE.exists()
    body = ENV_FILE.read_text(encoding="utf-8")
    assert f"REWARDS_DB_PATH={rewards_db}" in body
