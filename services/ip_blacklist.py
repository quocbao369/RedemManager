"""Shared blacklist of exit IPs already used for a redemption.

Mirrors the VIP behaviour: an IP is remembered for 30 days so the same exit
address is never spent twice on a Microsoft redemption.
"""
import sqlite3
import time
from typing import List, Optional

from database import Database


TTL_DAYS = 30
TTL_MS = TTL_DAYS * 24 * 60 * 60 * 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS redeemed_ips (
    ip              TEXT PRIMARY KEY,
    redeemed_at     INTEGER NOT NULL,
    account_email   TEXT NOT NULL DEFAULT '',
    success_count   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_redeemed_ips_redeemed_at ON redeemed_ips(redeemed_at);
"""


def _rewards_path() -> str:
    from config import settings
    return str(settings.rewards.db_path)


def ensure_table() -> None:
    with sqlite3.connect(_rewards_path()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def cleanup() -> int:
    """Drop entries older than the TTL. Returns how many were removed."""
    ensure_table()
    cutoff = int(time.time() * 1000) - TTL_MS
    with sqlite3.connect(_rewards_path()) as conn:
        cur = conn.execute("DELETE FROM redeemed_ips WHERE redeemed_at <= ?", (cutoff,))
        conn.commit()
        return cur.rowcount or 0


def is_blacklisted(ip: str) -> bool:
    if not ip:
        return False
    ensure_table()
    cutoff = int(time.time() * 1000) - TTL_MS
    with sqlite3.connect(_rewards_path()) as conn:
        row = conn.execute(
            "SELECT 1 FROM redeemed_ips WHERE ip = ? AND redeemed_at > ?",
            (ip, cutoff)).fetchone()
    return row is not None


def get_blacklisted() -> List[str]:
    ensure_table()
    cutoff = int(time.time() * 1000) - TTL_MS
    with sqlite3.connect(_rewards_path()) as conn:
        return [r[0] for r in conn.execute(
            "SELECT ip FROM redeemed_ips WHERE redeemed_at > ?", (cutoff,))]


def mark_redeemed(ip: str, email: str) -> Optional[int]:
    """Remember an exit IP; increments success_count when already present.

    Returns the new success_count, or None when there is nothing to record.
    """
    if not ip:
        return None
    ensure_table()
    now = int(time.time() * 1000)
    with sqlite3.connect(_rewards_path()) as conn:
        conn.execute("DELETE FROM redeemed_ips WHERE redeemed_at <= ?", (now - TTL_MS,))
        conn.execute(
            """
            INSERT INTO redeemed_ips (ip, redeemed_at, account_email, success_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(ip) DO UPDATE SET
                redeemed_at = excluded.redeemed_at,
                account_email = excluded.account_email,
                success_count = success_count + 1
            """,
            (ip, now, email or ""),
        )
        row = conn.execute(
            "SELECT success_count FROM redeemed_ips WHERE ip = ?", (ip,)).fetchone()
        conn.commit()
    return row[0] if row else None


def stat() -> dict:
    ensure_table()
    cutoff = int(time.time() * 1000) - TTL_MS
    with sqlite3.connect(_rewards_path()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM redeemed_ips").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM redeemed_ips WHERE redeemed_at > ?",
            (cutoff,)).fetchone()[0]
    return {"total": total, "active": active, "ttl_days": TTL_DAYS}