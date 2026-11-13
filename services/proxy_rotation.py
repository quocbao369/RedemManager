import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from database import Database
from services import ip_blacklist


SETTING_ROTATION_URL = "proxy_rotation_url"
SETTING_GLOBAL_PROXY = "proxy_global"


@dataclass
class RotationResult:
    success: bool
    proxy: Optional[str] = None
    ip: Optional[str] = None
    message: str = ""
    blacklisted: bool = False


def parse_proxy_string(raw: str) -> Optional[dict]:
    """Parse "ip:port:user:pass" (optionally http:// prefixed) into a dict."""
    if not raw:
        return None
    value = raw.strip()
    for scheme in ("https://", "http://"):
        if value.startswith(scheme):
            value = value[len(scheme):]
            break
    parts = value.split(":")
    if len(parts) < 2:
        return None
    return {
        "host": parts[0].strip(),
        "port": parts[1].strip(),
        "username": parts[2].strip() if len(parts) > 2 else "",
        "password": ":".join(parts[3:]).strip() if len(parts) > 3 else "",
    }


def get_setting(key: str, default: str = "") -> str:
    with Database.transaction() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str) -> None:
    with Database.transaction() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_global_proxy() -> Optional[dict]:
    return parse_proxy_string(get_setting(SETTING_GLOBAL_PROXY))


def remember_instance_ip(instance_db_id: int, ip: str) -> None:
    """Remember the exit IP a given instance is currently using."""
    if not ip:
        return
    set_setting(f"instance_ip_{instance_db_id}", ip)


def get_instance_ip(instance_db_id: int) -> str:
    return get_setting(f"instance_ip_{instance_db_id}")


def clear_instance_ip(instance_db_id: int) -> None:
    set_setting(f"instance_ip_{instance_db_id}", "")


def rotate_proxy(max_attempts: int = 5, timeout: int = 20,
                 avoid_blacklisted: bool = True,
                 max_wait: int = 60,
                 previous_ip: Optional[str] = None) -> RotationResult:
    """Call the rotation link to refresh the global proxy.

    Expected response (the user's service):
        {"status": "success", "proxy": "ip:port:user:pass", "ip": "1.2.3.4", ...}

    Handles the cases the VIP dashboard already handles:
      * the provider replies "not yet" with `timeRemaining` -> wait and retry
      * HTTP 429 rate limiting -> back off and retry
      * `status: success` but the IP did not actually change -> retry
      * exit IPs already spent on a previous redemption -> reject and retry

    `previous_ip` is the exit IP currently in use; when the provider reports
    success with that same IP the rotation is treated as failed.
    `max_wait` caps the total time spent waiting on cooldowns (seconds).
    """
    url = get_setting(SETTING_ROTATION_URL).strip()
    if not url:
        return RotationResult(False, message="Chua cau hinh link rotation proxy")

    rejected: List[str] = []
    waited = 0
    rate_limited = False
    last_ip = previous_ip

    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RedemManager/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status_code = resp.getcode()
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                rate_limited = True
                if waited >= max_wait:
                    return RotationResult(False, message="Rotation bi gioi han (429), da het gio cho")
                time.sleep(min(5, max_wait - waited))
                waited += 5
                continue
            return RotationResult(False, message=f"Rotation loi HTTP {e.code}: {e.reason}")
        except Exception as e:
            # Transient network/DNS hiccups (getaddrinfo, refused) usually clear
            # within a few seconds, so retry before giving up.
            if waited >= max_wait:
                return RotationResult(False, message=f"Loi goi link rotation: {e}")
            time.sleep(min(3, max_wait - waited))
            waited += 3
            continue

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Plain-text responses are tolerated: treat the body as ip:port:user:pass
            parsed = parse_proxy_string(body.strip())
            if parsed:
                return RotationResult(True, proxy=body.strip(), ip=parsed["host"])
            return RotationResult(False, message="Response khong phai JSON")

        status = str(data.get("status", "")).lower()
        proxy = data.get("proxy") or ""
        ip = data.get("ip") or ""

        if status == "success" and proxy:
            # The server may claim success while handing back the same exit IP.
            if last_ip and ip and ip == last_ip:
                if waited >= max_wait:
                    return RotationResult(False, message="IP xoay khong doi")
                time.sleep(min(3, max_wait - waited))
                waited += 3
                continue

            if avoid_blacklisted and ip and ip_blacklist.is_blacklisted(ip):
                rejected.append(ip)
                if waited >= max_wait:
                    return RotationResult(
                        False, blacklisted=True,
                        message=f"{len(rejected)} IP da dung truoc, khong lay duoc IP moi: "
                                f"{', '.join(rejected[:5])}")
                time.sleep(min(3 + attempt * 2, 15))
                waited += 3
                continue

            last_ip = ip or None
            return RotationResult(True, proxy=proxy, ip=ip or None,
                                  message=data.get("message", ""))

        # Refused: either a cooldown is still running, or a hard failure.
        remaining = data.get("timeRemaining") or 0
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            remaining = 0

        if remaining > 0:
            wait = min(remaining + 2, max_wait - waited)
            if wait <= 0:
                return RotationResult(
                    False,
                    message=f"Chua toi thoi gian xoay (con {remaining}s), da cho toi da "
                            f"{max_wait}s")
            time.sleep(wait)
            waited += wait
            continue

        if attempt < max_attempts:
            wait = min(5, max_wait - waited)
            if wait <= 0:
                break
            time.sleep(wait)
            waited += wait

    if rejected:
        return RotationResult(
            False, blacklisted=True,
            message=f"{len(rejected)} IP da dung truoc, khong lay duoc IP moi: "
                    f"{', '.join(rejected[:5])}")
    if rate_limited:
        return RotationResult(False, message="Rotation bi gioi tan (429), thu lai sau")
    return RotationResult(False, message="Xoay proxy that bai")


proxy_rotation_service = None