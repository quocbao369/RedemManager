import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import settings


class Database:
    _local = threading.local()
    _init_lock = threading.Lock()
    _initialized = False
    # Bumped when the Settings page re-points the app at another DB file
    # (Settings > Load). Every per-thread connection opened under an older
    # generation is discarded on its next use.
    _generation = 0

    @classmethod
    def get_connection(cls) -> sqlite3.Connection:
        conn = getattr(cls._local, "conn", None)
        if conn is None or getattr(cls._local, "generation", None) != cls._generation:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            conn = sqlite3.connect(
                settings.database.path,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            cls._local.conn = conn
            cls._local.generation = cls._generation
        return conn

    @classmethod
    @contextmanager
    def transaction(cls):
        conn = cls.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @classmethod
    def reopen(cls) -> None:
        """Point the app at the currently configured DB file.

        Worker threads keep one connection each in threading.local; paths
        are only read when a connection is created, so switching files on
        the Settings page (Settings > Load) has to discard them all. The
        schema runs again on first use against the new file.
        """
        with cls._init_lock:
            cls._generation += 1
            cls._initialized = False

    @classmethod
    def initialize(cls):
        with cls._init_lock:
            if cls._initialized:
                return
            conn = cls.get_connection()
            cursor = conn.cursor()

            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS instances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ld_index INTEGER NOT NULL,
                    ld_version INTEGER NOT NULL DEFAULT 9,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'stopped',
                    serial TEXT,
                    android_version TEXT,
                    cpu_cores INTEGER,
                    memory_mb INTEGER,
                    resolution TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ld_version, ld_index)
                );

                CREATE TABLE IF NOT EXISTS proxy_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    username TEXT,
                    password_encrypted TEXT,
                    protocol TEXT DEFAULT 'http',
                    is_active BOOLEAN DEFAULT 1,
                    last_tested_at TIMESTAMP,
                    last_test_result TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS workflows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id INTEGER NOT NULL,
                    proxy_profile_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    current_step TEXT,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (instance_id) REFERENCES instances(id),
                    FOREIGN KEY (proxy_profile_id) REFERENCES proxy_profiles(id)
                );

                CREATE TABLE IF NOT EXISTS workflow_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER NOT NULL,
                    step_name TEXT NOT NULL,
                    step_order INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    output_data TEXT,
                    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER,
                    instance_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL,
                    FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS account_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_email TEXT UNIQUE NOT NULL,
                    instance_id INTEGER,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER,
                    instance_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL,
                    FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_workflows_instance ON workflows(instance_id);
                CREATE INDEX IF NOT EXISTS idx_workflow_steps_workflow ON workflow_steps(workflow_id);
                CREATE INDEX IF NOT EXISTS idx_activity_logs_workflow ON activity_logs(workflow_id);
                CREATE INDEX IF NOT EXISTS idx_activity_logs_instance ON activity_logs(instance_id);
                CREATE INDEX IF NOT EXISTS idx_activity_logs_created ON activity_logs(created_at);
                CREATE INDEX IF NOT EXISTS idx_account_mapping_instance ON account_mapping(instance_id);
            """)

            cls._migrate_instances_schema(conn)

            conn.commit()
            cls._initialized = True

    @classmethod
    def _migrate_instances_schema(cls, conn: sqlite3.Connection) -> None:
        """Rebuild `instances` when it still uses the pre-LD14 schema.

        Old databases keyed an instance by ld_index alone. With a second
        LDPlayer installation (version 14) the same index can exist in both
        installs, so the key becomes (ld_version, ld_index). SQLite cannot
        drop a UNIQUE constraint, hence the table rebuild.

        Child tables (workflows / activity_logs / account_mapping) hold
        FOREIGN KEY ... ON DELETE SET NULL references to instances(id); the
        rebuild runs with foreign_keys OFF so dropping the parent table does
        not null out every mapping row in the process.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(instances)").fetchall()}
        if "ld_version" in columns:
            return

        rows = conn.execute(
            "SELECT id, ld_index, name, status, serial, android_version, "
            "cpu_cores, memory_mb, resolution, created_at, updated_at FROM instances"
        ).fetchall()

        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute("DROP TABLE instances")
            conn.execute("""
                CREATE TABLE instances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ld_index INTEGER NOT NULL,
                    ld_version INTEGER NOT NULL DEFAULT 9,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'stopped',
                    serial TEXT,
                    android_version TEXT,
                    cpu_cores INTEGER,
                    memory_mb INTEGER,
                    resolution TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ld_version, ld_index)
                )
            """)
            conn.executemany(
                "INSERT INTO instances (id, ld_index, ld_version, name, status, serial, "
                "android_version, cpu_cores, memory_mb, resolution, created_at, updated_at) "
                "VALUES (?, ?, 9, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(r) for r in rows],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def close_all(cls):
        if hasattr(cls._local, "conn") and cls._local.conn:
            cls._local.conn.close()
            cls._local.conn = None


Database.initialize()


def dict_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def log_activity(
    level: str,
    message: str,
    workflow_id: Optional[int] = None,
    instance_id: Optional[int] = None,
    metadata: Optional[Dict] = None,
):
    import json
    with Database.transaction() as conn:
        conn.execute(
            "INSERT INTO activity_logs (workflow_id, instance_id, level, message, metadata) VALUES (?, ?, ?, ?, ?)",
            (workflow_id, instance_id, level, message, json.dumps(metadata) if metadata else None),
        )