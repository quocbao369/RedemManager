import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the application at a throw-away database BEFORE any project module is
# imported. Without this, the test fixtures run "DELETE FROM instances" against
# the real data/redem.db, which - thanks to ON DELETE SET NULL - silently wipes
# every row of account_mapping in production.
_TMP_DIR = tempfile.mkdtemp(prefix="redem_test_db_")
os.environ["DATABASE_PATH"] = str(Path(_TMP_DIR) / "test_redem.db")
os.environ.setdefault("REWARDS_DB_PATH", str(Path(_TMP_DIR) / "test_rewards.db"))

import pytest  # noqa: E402

from database import Database  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _isolated_database():
    Database.initialize()
    yield