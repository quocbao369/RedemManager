import socket
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict, Any
from pathlib import Path
from config import settings
from database import Database, dict_from_row, log_activity
from services.adb_service import adb_service


@dataclass
class ProxyProfile:
    id: Optional[int]
    name: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"
    is_active: bool = True
    last_tested_at: Optional[str] = None
    last_test_result: Optional[str] = None


class ProxyService:
    def __init__(self):
        pass

    def create_profile(self, name: str, host: str, port: int, username: Optional[str] = None,
                       password: Optional[str] = None, protocol: str = "http") -> ProxyProfile:
        if not self._validate_host_port(host, port):
            raise ValueError("Invalid host or port")

        with Database.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO proxy_profiles (name, host, port, username, password_encrypted, protocol, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (name, host, port, username, self._encrypt_password(password) if password else None, protocol),
            )
            profile_id = cursor.lastrowid

        return self.get_profile(profile_id)

    def get_profile(self, profile_id: int) -> Optional[ProxyProfile]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM proxy_profiles WHERE id = ?", (profile_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_profile(row)
        return None

    def get_profile_by_name(self, name: str) -> Optional[ProxyProfile]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM proxy_profiles WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                return self._row_to_profile(row)
        return None

    def list_profiles(self) -> list[ProxyProfile]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM proxy_profiles ORDER BY created_at DESC")
            return [self._row_to_profile(row) for row in cursor.fetchall()]

    def update_profile(self, profile_id: int, **kwargs) -> Optional[ProxyProfile]:
        allowed = {"host", "port", "username", "password", "protocol", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_profile(profile_id)

        if "password" in updates:
            updates["password_encrypted"] = self._encrypt_password(updates.pop("password"))

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [profile_id]

        with Database.transaction() as conn:
            conn.execute(f"UPDATE proxy_profiles SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id: int) -> bool:
        with Database.transaction() as conn:
            cursor = conn.execute("DELETE FROM proxy_profiles WHERE id = ?", (profile_id,))
            return cursor.rowcount > 0

    def test_proxy(self, host: str, port: int, username: Optional[str] = None,
                   password: Optional[str] = None, timeout: int = 10) -> Dict[str, Any]:
        result = {"connected": False, "latency_ms": None, "error": None}

        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            result["connected"] = True
        except socket.timeout:
            result["error"] = "Connection timeout"
            return result
        except ConnectionRefusedError:
            result["error"] = "Connection refused"
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

        if username and password:
            try:
                import urllib.request
                proxy_handler = urllib.request.ProxyHandler({
                    "http": f"http://{username}:{password}@{host}:{port}",
                    "https": f"http://{username}:{password}@{host}:{port}",
                })
                opener = urllib.request.build_opener(proxy_handler)
                opener.open(settings.apps.connectivity_test_url, timeout=timeout)
                result["connected"] = True
            except Exception as e:
                result["error"] = f"Auth test failed: {e}"
                result["connected"] = False

        return result

    def test_profile(self, profile_id: int) -> Dict[str, Any]:
        profile = self.get_profile(profile_id)
        if not profile:
            return {"connected": False, "error": "Profile not found"}

        result = self.test_proxy(profile.host, profile.port, profile.username, profile.password)

        with Database.transaction() as conn:
            from datetime import datetime
            conn.execute(
                "UPDATE proxy_profiles SET last_tested_at = ?, last_test_result = ? WHERE id = ?",
                (datetime.now().isoformat(), "success" if result["connected"] else f"failed: {result.get('error')}", profile_id),
            )

        return result

    def configure_device_proxy(self, serial: str, profile: ProxyProfile) -> bool:
        if not adb_service.set_proxy(serial, profile.host, profile.port):
            return False

        if profile.username and profile.password:
            auth_result = adb_service.shell(serial, f"settings put global global_http_proxy_user {profile.username}")
            auth_result = adb_service.shell(serial, f"settings put global global_http_proxy_password {profile.password}")

        return True

    def clear_device_proxy(self, serial: str) -> bool:
        return adb_service.clear_proxy(serial)

    def verify_device_proxy(self, serial: str) -> Dict[str, Any]:
        proxy = adb_service.get_proxy(serial)
        connectivity = adb_service.check_connectivity(serial)
        return {
            "configured_proxy": proxy,
            "connectivity_ok": connectivity,
        }

    def _validate_host_port(self, host: str, port: int) -> bool:
        if not host or not isinstance(port, int):
            return False
        if port < 1 or port > 65535:
            return False
        try:
            socket.inet_aton(host)
            return True
        except OSError:
            pass
        try:
            socket.gethostbyname(host)
            return True
        except socket.gaierror:
            return False

    def _encrypt_password(self, password: Optional[str]) -> Optional[str]:
        if not password:
            return None
        import base64
        return base64.b64encode(password.encode()).decode()

    def _decrypt_password(self, encrypted: Optional[str]) -> Optional[str]:
        if not encrypted:
            return None
        import base64
        try:
            return base64.b64decode(encrypted.encode()).decode()
        except Exception:
            return None

    def _row_to_profile(self, row) -> ProxyProfile:
        return ProxyProfile(
            id=row["id"],
            name=row["name"],
            host=row["host"],
            port=row["port"],
            username=row["username"],
            password=self._decrypt_password(row["password_encrypted"]),
            protocol=row["protocol"],
            is_active=bool(row["is_active"]),
            last_tested_at=row["last_tested_at"],
            last_test_result=row["last_test_result"],
        )


proxy_service = ProxyService()