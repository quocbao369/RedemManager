import subprocess
import re
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from config import settings


@dataclass
class ADBDevice:
    serial: str
    status: str
    model: Optional[str] = None
    android_version: Optional[str] = None


from config import settings


class ADBService:
    # Live reads from config so a path saved in Settings takes effect without
    # restarting the server.
    @property
    def adb_path(self) -> Path:
        return settings.ldplayer.adb_path

    @property
    def timeout(self) -> int:
        return settings.ldplayer.adb_connect_timeout

    def _run(self, args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        if timeout is None:
            timeout = self.timeout
        try:
            return subprocess.run(
                [str(self.adb_path)] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ADB command timed out: {' '.join(args)}") from e
        except FileNotFoundError:
            raise RuntimeError(f"adb not found at {self.adb_path}")

    def restart_server(self) -> None:
        """Restart the ADB server - used to recover from stuck/offline states."""
        for args in (["kill-server"], ["start-server"]):
            try:
                self._run(args, timeout=30)
            except Exception:
                pass

    def get_adb_status(self) -> Dict[str, Any]:
        """Return a summary of the current ADB state for diagnostics."""
        status: Dict[str, Any] = {"adb_path": str(self.adb_path), "exists": self.adb_path.exists()}
        try:
            result = self._run(["devices"], timeout=15)
            devices = []
            for line in result.stdout.strip().split("\n")[1:]:
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    devices.append({"serial": parts[0], "status": parts[1]})
            status["devices"] = devices
            status["offline"] = [d["serial"] for d in devices if d["status"] != "device"]
        except Exception as e:
            status["error"] = str(e)
        return status

    def reconnect(self, serial: Optional[str] = None) -> None:
        """Try to bring device(s) back online."""
        if serial:
            try:
                self._run(["reconnect", "device"], timeout=15)
                self._run(["disconnect", serial], timeout=15)
                self._run(["connect", serial], timeout=15)
            except Exception:
                pass

    def ensure_device(self, serial: str, timeout: Optional[int] = None) -> bool:
        """Make sure a serial is connected and online, recovering it if needed.

        LDPlayer keeps devices on 127.0.0.1:<5555 + 2*index>; ADB can leave them
        in the "offline" state after an instance restart, so we reconnect.
        """
        if timeout is None:
            timeout = self.timeout
        deadline = time.time() + timeout
        restarted = False

        def _online() -> bool:
            try:
                r = self._run(["devices"], timeout=20)
            except Exception:
                return False
            for line in r.stdout.strip().split("\n")[1:]:
                if line.startswith(serial):
                    return line.split()[1] == "device"
            return False

        while time.time() < deadline:
            if _online():
                return True
            host, _, port = serial.partition(":")
            port = port or "5555"
            try:
                self._run(["disconnect", serial], timeout=20)
            except Exception:
                pass
            try:
                self._run(["connect", f"{host}:{port}"], timeout=20)
            except Exception:
                pass
            time.sleep(4)

            # Halfway through, recycle the ADB server: an offline transport
            # is often only cleared by restarting it.
            if not restarted and time.time() > deadline - (timeout / 2):
                restarted = True
                self.restart_server()
                time.sleep(3)

        return _online()

    def _online_any(self) -> bool:
        """True when at least one device is online."""
        try:
            r = self._run(["devices"], timeout=20)
        except Exception:
            return False
        for line in (r.stdout or "").strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return True
        return False

    def install_multi(self, serial: str, apk_paths: List[Path], timeout: int = 900) -> Tuple[bool, str]:
        """Install a base APK plus its splits via `adb install-multiple`."""
        args = ["-s", serial, "install-multiple", "-r", "-g"] + [str(p) for p in apk_paths]
        try:
            result = self._run(args, timeout=timeout)
        except RuntimeError as e:
            return False, str(e)
        out = (result.stdout or "") + (result.stderr or "")
        # adb exits 0 even on failure, so the text is the only reliable signal.
        if "Success" in out:
            return True, "Success"
        return False, out.strip()[-300:] or f"exit {result.returncode}"

    def list_devices(self) -> List[ADBDevice]:
        result = self._run(["devices", "-l"])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list devices: {result.stderr}")

        devices = []
        for line in result.stdout.strip().split("\n")[1:]:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                model = None
                android_version = None
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part[6:]
                    elif part.startswith("device:") and not model:
                        model = part[7:]
                devices.append(ADBDevice(serial=serial, status=status, model=model))
        return devices

    def get_device_info(self, serial: str) -> Dict[str, Any]:
        info = {}
        props = [
            ("model", "ro.product.model"),
            ("brand", "ro.product.brand"),
            ("android_version", "ro.build.version.release"),
            ("api_level", "ro.build.version.sdk"),
            ("arch", "ro.product.cpu.abi"),
            ("manufacturer", "ro.product.manufacturer"),
        ]
        for key, prop in props:
            result = self._run(["-s", serial, "shell", "getprop", prop])
            if result.returncode == 0:
                info[key] = result.stdout.strip()
        return info

    def install_apk(self, serial: str, apk_path: Path, replace: bool = True, grant_permissions: bool = True) -> bool:
        args = ["-s", serial, "install"]
        if replace:
            args.append("-r")
        if grant_permissions:
            args.append("-g")
        args.append(str(apk_path))
        try:
            result = self._run(args, timeout=300)
        except RuntimeError:
            return False
        out = (result.stdout or "") + (result.stderr or "")
        # adb returns 0 even when the install failed - check the message instead.
        return "Success" in out

    def uninstall_package(self, serial: str, package_name: str) -> bool:
        result = self._run(["-s", serial, "uninstall", package_name])
        return result.returncode == 0

    def is_package_installed(self, serial: str, package_name: str) -> bool:
        result = self._run(["-s", serial, "shell", "pm", "list", "packages", package_name])
        return package_name in result.stdout

    def launch_package(self, serial: str, package_name: str) -> bool:
        result = self._run(["-s", serial, "shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"])
        return result.returncode == 0

    def kill_package(self, serial: str, package_name: str) -> bool:
        result = self._run(["-s", serial, "shell", "am", "force-stop", package_name])
        return result.returncode == 0

    def get_installed_packages(self, serial: str, filter_prefix: Optional[str] = None) -> List[str]:
        result = self._run(["-s", serial, "shell", "pm", "list", "packages"])
        packages = []
        for line in result.stdout.strip().split("\n"):
            if line.startswith("package:"):
                pkg = line[8:].strip()
                if not filter_prefix or pkg.startswith(filter_prefix):
                    packages.append(pkg)
        return packages

    def shell(self, serial: str, command: str) -> subprocess.CompletedProcess:
        return self._run(["-s", serial, "shell"] + command.split())

    def push(self, serial: str, local_path: Path, remote_path: str) -> bool:
        result = self._run(["-s", serial, "push", str(local_path), remote_path])
        return result.returncode == 0

    def pull(self, serial: str, remote_path: str, local_path: Path) -> bool:
        result = self._run(["-s", serial, "pull", remote_path, str(local_path)])
        return result.returncode == 0

    def set_proxy(self, serial: str, host: str, port: int) -> bool:
        result = self._run(["-s", serial, "shell", "settings", "put", "global", "http_proxy", f"{host}:{port}"])
        if result.returncode != 0:
            return False
        result = self._run(["-s", serial, "shell", "settings", "put", "global", "global_http_proxy_host", host])
        if result.returncode != 0:
            return False
        result = self._run(["-s", serial, "shell", "settings", "put", "global", "global_http_proxy_port", str(port)])
        return result.returncode == 0

    def clear_proxy(self, serial: str) -> bool:
        result = self._run(["-s", serial, "shell", "settings", "delete", "global", "http_proxy"])
        self._run(["-s", serial, "shell", "settings", "delete", "global", "global_http_proxy_host"])
        self._run(["-s", serial, "shell", "settings", "delete", "global", "global_http_proxy_port"])
        return result.returncode == 0

    def get_proxy(self, serial: str) -> Optional[str]:
        result = self._run(["-s", serial, "shell", "settings", "get", "global", "http_proxy"])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    def check_connectivity(self, serial: str,
                            test_url: str = None) -> bool:
        if test_url is None:
            test_url = settings.apps.connectivity_test_url
        result = self._run(["-s", serial, "shell", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10", test_url])
        return result.returncode == 0 and result.stdout.strip() == "204"


adb_service = ADBService()