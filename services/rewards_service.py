import json
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from pathlib import Path
from config import settings
from database import Database, dict_from_row


@dataclass
class RewardAccount:
    email: str
    password: str
    totp_secret: Optional[str]
    recovery_email: Optional[str]
    geo_locale: Optional[str]
    lang_code: Optional[str]
    proxy: Optional[str]
    tag: Optional[str]
    account_group: Optional[str]
    created_at: Optional[int]
    tag: Optional[str]
    coupon_discount: int
    ready_to_redeem: bool
    points_needed: int
    points: Optional[int]
    suspended: Optional[int]
    rank: Optional[str]
    instance_id: Optional[int]
    instance_name: Optional[str]
    instance_status: Optional[str]
    proxy_parsed: Optional[Dict[str, Any]]
    proxy_display: Optional[str] = None
    proxy_type: Optional[str] = None
    initial_points: Optional[int] = None
    collected_points: Optional[int] = None


# Redemption thresholds, kept in sync with the VIP dashboard
# (scripts/main/dashboard.js): 'Car' needs 2925 points, 'Mart' and 'Jollibee'
# need 5460, minus whatever coupon discount the account carries.
POINTS_CAR = 2925
POINTS_MARKET = 5460
REDEEM_TAGS = ("Car", "Mart", "Jollibee")


def points_required(tag: Optional[str], coupon_discount: int = 0) -> int:
    base = POINTS_CAR if (tag or "").strip().lower() == "car" else POINTS_MARKET
    return max(0, base - (coupon_discount or 0))


def is_ready(tag: Optional[str], coupon_discount: int, points: Optional[int]) -> bool:
    """Whether the account has enough points to redeem, as VIP defines it."""
    if not tag or tag not in REDEEM_TAGS or points is None:
        return False
    return points >= points_required(tag, coupon_discount)


class RewardsService:
    def __init__(self):
        self.db_path = settings.rewards.db_path

    def set_db_path(self, path) -> None:
        """Point the service at another rewards database (Settings > Load).

        Queries open a fresh sqlite3 connection against self.db_path each
        time, so re-pointing here is all it takes - the next get_accounts()
        reads the new file.
        """
        self.db_path = Path(path)

    def _parse_proxy(self, proxy_str: Optional[str]) -> Optional[Dict[str, Any]]:
        if not proxy_str:
            return None
        # Try to parse as JSON first (actual format in DB)
        try:
            data = json.loads(proxy_str)
            if isinstance(data, dict):
                # Format: {"url": "http://14.224.219.62", "port": "47440",
                #          "username": "...", "password": "...", "isProxyV6": false}
                url = data.get("url", "") or ""
                for scheme in ("https://", "http://"):
                    if url.startswith(scheme):
                        url = url[len(scheme):]
                        break
                host = url.strip()
                port = str(data.get("port", "") or "")
                username = data.get("username", "") or ""
                password = data.get("password", "") or ""
                if host and port:
                    return {
                        "host": host,
                        "port": port,
                        "username": username,
                        "password": password,
                        # Use the authoritative flag stored in the DB
                        "is_v6": bool(data.get("isProxyV6", False)),
                    }
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: try ip:port:user:pass format
        parts = proxy_str.split(":")
        if len(parts) >= 4:
            host = parts[0]
            return {
                "host": host,
                "port": parts[1],
                "username": parts[2],
                "password": ":".join(parts[3:]),
                "is_v6": ":" in host,
            }
        return None

    def get_accounts(self) -> List[RewardAccount]:
        """Load accounts from rewards_data.db and join with RedemManager mapping."""
        accounts = []

        # Connect to rewards_data.db
        if not self.db_path.exists():
            return accounts

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT a.*, s.points, s.suspended, s.rank,
                       s.initial_points, s.collected_points
                FROM accounts a
                LEFT JOIN account_status s ON a.email = s.email
                ORDER BY a.created_at DESC
            """)
            reward_accounts = cursor.fetchall()

        # Get mappings from RedemManager DB
        mappings = {}
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT account_email, instance_id FROM account_mapping")
            for row in cursor.fetchall():
                mappings[row["account_email"]] = row["instance_id"]

        # Get instance details
        instance_details = {}
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT id, ld_index, name, status FROM instances")
            for row in cursor.fetchall():
                instance_details[row["id"]] = dict_from_row(row)

        # Combine
        for row in reward_accounts:
            email = row["email"]
            instance_id = mappings.get(email)
            inst = instance_details.get(instance_id) if instance_id else None

            proxy_parsed = self._parse_proxy(row["proxy"])
            proxy_display = None
            proxy_type = None
            if proxy_parsed:
                proxy_display = f"{proxy_parsed['host']}:{proxy_parsed['port']}:{proxy_parsed['username']}:{proxy_parsed['password']}"
                # Use the isProxyV6 flag stored in the account record
                proxy_type = 'v6' if proxy_parsed.get('is_v6') else 'v4'

            accounts.append(RewardAccount(
                email=email,
                password=row["password"],
                totp_secret=row["totp_secret"],
                recovery_email=row["recovery_email"],
                geo_locale=row["geo_locale"],
                lang_code=row["lang_code"],
                proxy=row["proxy"],
                tag=row["tag"],
                account_group=row["account_group"],
                created_at=row["created_at"],
                coupon_discount=int(row["couponDiscount"] or 0),
                ready_to_redeem=is_ready(row["tag"], row["couponDiscount"] or 0,
                                        row["points"]),
                points_needed=points_required(row["tag"], row["couponDiscount"] or 0),
                points=row["points"],
                initial_points=row["initial_points"],
                collected_points=row["collected_points"],
                suspended=row["suspended"],
                rank=row["rank"],
                instance_id=instance_id,
                instance_name=inst["name"] if inst else None,
                instance_status=inst["status"] if inst else None,
                proxy_parsed=proxy_parsed,
                proxy_display=proxy_display,
                proxy_type=proxy_type,
            ))

        return accounts

    def get_mapping(self, email: str) -> Optional[int]:
        if not email:
            return None
        with Database.transaction() as conn:
            row = conn.execute(
                "SELECT instance_id FROM account_mapping WHERE LOWER(account_email) = ?",
                (email.strip().lower(),)).fetchone()
        return row["instance_id"] if row else None

    def assign_instance(self, email: str, instance_id: Optional[int]) -> bool:
        with Database.transaction() as conn:
            if instance_id is None:
                conn.execute("DELETE FROM account_mapping WHERE account_email = ?", (email,))
            else:
                conn.execute("""
                    INSERT INTO account_mapping (account_email, instance_id, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(account_email) DO UPDATE SET
                        instance_id = excluded.instance_id,
                        updated_at = CURRENT_TIMESTAMP
                """, (email, instance_id))
            return True

    def save_redeem_result(self, email: str, points: Optional[int], voucher_url: str,
                           reward_type: str) -> Dict[str, Any]:
        """Persist a redemption exactly the way the VIP dashboard does.

        * accounts: redem_last, redem_url, redem_url_reset, couponDiscount
        * account_status: points, last_order
        * vouchers: insert unless the URL already exists (UNIQUE)
        """
        from datetime import datetime, timezone

        now_ms = int(datetime.now().timestamp() * 1000)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        result: Dict[str, Any] = {"voucher_inserted": False, "voucher_exists": False}

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # --- accounts -------------------------------------------------
            cur.execute(
                """
                UPDATE accounts
                   SET redem_last = ?, redem_url = ?, redem_url_reset = 0,
                       couponDiscount = 0, updated_at = ?
                 WHERE email = ?
                """,
                (now_iso, voucher_url or "", now_ms, email),
            )
            result["accounts_updated"] = cur.rowcount > 0

            # --- account_status -------------------------------------------
            if points is not None:
                cur.execute(
                    """
                    INSERT INTO account_status (email, points, last_order, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(email) DO UPDATE SET
                        points = excluded.points,
                        last_order = excluded.last_order,
                        updated_at = excluded.updated_at
                    """,
                    (email, points, now_iso, now_ms),
                )
            else:
                cur.execute(
                    "UPDATE account_status SET last_order = ?, updated_at = ? WHERE email = ?",
                    (now_iso, now_ms, email),
                )
            result["status_updated"] = cur.rowcount > 0

            # --- vouchers -------------------------------------------------
            if voucher_url:
                existing = cur.execute(
                    "SELECT id FROM vouchers WHERE voucher_url = ?", (voucher_url,)
                ).fetchone()
                if existing:
                    result["voucher_exists"] = True
                    result["voucher_id"] = existing["id"]
                else:
                    cur.execute(
                        """
                        INSERT INTO vouchers
                            (email, redem_date, type, img_url, voucher_url,
                             status, price_sell, created_at, updated_at)
                        VALUES (?, ?, ?, '', ?, 'stock', ?, ?, ?)
                        """,
                        (email, now_iso, reward_type or "N/A", voucher_url,
                         default_price_for(reward_type), now_ms, now_ms),
                    )
                    result["voucher_inserted"] = True
                    result["voucher_id"] = cur.lastrowid

            conn.commit()

        return result

    def find_account(self, email: str):
        """Look up an account ignoring case (emails may be typed in any case)."""
        if not email:
            return None
        target = email.strip().casefold()
        for a in self.get_accounts():
            if a.email and a.email.strip().casefold() == target:
                return a
        return None

    def get_instance_mapping(self, instance_id: int) -> Optional[str]:
        with Database.transaction() as conn:
            cursor = conn.execute(
                "SELECT account_email FROM account_mapping WHERE instance_id = ?",
                (instance_id,))
            row = cursor.fetchone()
            return row["account_email"] if row else None


def default_price_for(reward_type: str) -> int:
    return 30000 if reward_type == "GrabCar" else 70000


rewards_service = RewardsService()