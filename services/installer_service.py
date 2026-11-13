import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from config import settings
from services.adb_service import adb_service
from services.ldplayer_service import ldplayer_service


@dataclass
class XAPKInfo:
    manifest: Dict
    apks: List[Path]
    base_apk: Optional[Path] = None
    split_apks: List[Path] = None

    def __post_init__(self):
        if self.split_apks is None:
            self.split_apks = []


class InstallerService:
    # Read live from config: the paths are editable from the Settings form,
    # and caching them here would keep installing the old APK.
    @property
    def bing_apk(self) -> Path:
        return settings.apps.bing_apk_path

    @property
    def oxyproxy_xapk(self) -> Path:
        return settings.apps.oxyproxy_xapk_path

    def verify_apk(self, apk_path: Path) -> Dict[str, any]:
        result = {"valid": False, "package_name": None, "version_code": None, "version_name": None, "error": None}
        if not apk_path.exists():
            result["error"] = f"APK not found: {apk_path}"
            return result

        try:
            aapt = Path(settings.ldplayer.ldconsole_path).parent / "aapt.exe"
            cmd = [str(aapt) if aapt.exists() else "aapt", "dump", "badging", str(apk_path)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                result["error"] = f"aapt failed: {proc.stderr}"
                return result

            output = proc.stdout
            for line in output.split("\n"):
                if line.startswith("package:"):
                    import re
                    match = re.search(r"name='([^']+)'", line)
                    if match:
                        result["package_name"] = match.group(1)
                    match = re.search(r"versionCode='(\d+)'", line)
                    if match:
                        result["version_code"] = match.group(1)
                    match = re.search(r"versionName='([^']+)'", line)
                    if match:
                        result["version_name"] = match.group(1)
                elif "application-label:" in line:
                    pass

            result["valid"] = True
        except Exception as e:
            result["error"] = str(e)
        return result

    def _extract_xapk(self, xapk_path: Path) -> Path:
        """Extract a XAPK to a persistent temp dir and return its path."""
        if not xapk_path.exists():
            raise FileNotFoundError(f"XAPK not found: {xapk_path}")
        tmpdir = Path(tempfile.mkdtemp(prefix="xapk_"))
        with zipfile.ZipFile(xapk_path, "r") as z:
            z.extractall(tmpdir)
        return tmpdir

    def _cleanup_extract(self, tmpdir: Path) -> None:
        shutil.rmtree(tmpdir, ignore_errors=True)

    def analyze_xapk(self, xapk_path: Path) -> XAPKInfo:
        tmpdir = self._extract_xapk(xapk_path)
        try:
            manifest_path = tmpdir / "manifest.json"
            manifest = {}
            if manifest_path.exists():
                import json
                manifest = json.loads(manifest_path.read_text())

            apks = sorted(tmpdir.glob("*.apk"), key=lambda p: p.stat().st_size, reverse=True)
            if not apks:
                raise ValueError("No APK found inside XAPK")

            # The base APK holds classes.dex and is therefore the largest one.
            # Split APKs are also described in manifest.json under "split_apks".
            split_names = set()
            for entry in manifest.get("split_apks", []) or []:
                if isinstance(entry, dict):
                    name = entry.get("file") or entry.get("name") or entry.get("path")
                    if name:
                        split_names.add(Path(name).name)
                elif isinstance(entry, str):
                    split_names.add(Path(entry).name)

            base_apk = next((a for a in apks if a.name not in split_names), None) or apks[0]
            split_apks = [a for a in apks if a != base_apk]
            return XAPKInfo(manifest=manifest, apks=apks, base_apk=base_apk, split_apks=split_apks)
        finally:
            self._cleanup_extract(tmpdir)

    def install_xapk(self, serial: str, xapk_path: Path) -> Tuple[bool, str]:
        info = self.analyze_xapk(xapk_path)
        if not info.base_apk:
            return False, "No base APK found in XAPK"

        success = adb_service.install_apk(serial, info.base_apk)
        if not success:
            return False, f"Failed to install base APK: {info.base_apk.name}"

        for split_apk in info.split_apks:
            success = adb_service.install_apk(serial, split_apk)
            if not success:
                return False, f"Failed to install split APK: {split_apk.name}"

        return True, f"Installed {len(info.apks)} APK(s) from XAPK"

    def install_bing(self, serial: str) -> Tuple[bool, str]:
        if not self.bing_apk.exists():
            return False, f"Bing APK not found: {self.bing_apk}"

        verify = self.verify_apk(self.bing_apk)
        if not verify["valid"]:
            return False, f"Invalid Bing APK: {verify['error']}"

        success = adb_service.install_apk(serial, self.bing_apk)
        if success:
            if adb_service.is_package_installed(serial, settings.apps.bing_package_name):
                return True, f"Bing installed successfully (v{verify['version_name']})"
            return False, "APK installed but package not found"
        return False, "Failed to install Bing APK"

    def install_oxyproxy(self, serial: str) -> Tuple[bool, str]:
        if not self.oxyproxy_xapk.exists():
            return False, f"OxyProxy XAPK not found: {self.oxyproxy_xapk}"

        return self.install_xapk(serial, self.oxyproxy_xapk)

    def is_installed_to_index(self, index: int, package_name: str) -> bool:
        """Check whether a package is already present in an instance."""
        serial = ldplayer_service.get_serial(index)
        if not adb_service.ensure_device(serial, timeout=30):
            return False
        try:
            result = adb_service._run(
                ["-s", serial, "shell", "pm", "list", "packages", package_name],
                timeout=60)
        except Exception:
            return False
        out = result.stdout or ""
        return any(line.strip() == f"package:{package_name}" for line in out.splitlines())

    def install_apk_to_index(self, index: int, apk_path: Path) -> Tuple[bool, str]:
        """Install an APK into an LDPlayer instance via ldconsole (no ADB required)."""
        return ldplayer_service.install_app(index, apk_path)

    def install_xapk_to_index(self, index: int, xapk_path: Path) -> Tuple[bool, str]:
        tmpdir = self._extract_xapk(xapk_path)
        try:
            manifest_path = tmpdir / "manifest.json"
            manifest = {}
            if manifest_path.exists():
                import json
                manifest = json.loads(manifest_path.read_text())

            apks = sorted(tmpdir.glob("*.apk"), key=lambda p: p.stat().st_size, reverse=True)
            if not apks:
                return False, "No APK found inside XAPK"

            split_names = set()
            for entry in manifest.get("split_apks", []) or []:
                if isinstance(entry, dict):
                    name = entry.get("file") or entry.get("name") or entry.get("path")
                    if name:
                        split_names.add(Path(name).name)
                elif isinstance(entry, str):
                    split_names.add(Path(entry).name)

            base_apk = next((a for a in apks if a.name not in split_names), None) or apks[0]
            ordered = [base_apk] + [a for a in apks if a != base_apk]

            serial = ldplayer_service.get_serial(index)
            if not adb_service.ensure_device(serial):
                return False, f"ADB device {serial} is not online"

            # A single install-multiple keeps the splits attached to the base APK.
            ok, msg = adb_service.install_multi(serial, ordered)
            if ok:
                return True, f"Installed {len(ordered)} APK(s) from XAPK ({base_apk.name})"
            return False, msg
        finally:
            self._cleanup_extract(tmpdir)

    def install_bing_to_index(self, index: int) -> Tuple[bool, str]:
        if not self.bing_apk.exists():
            return False, f"Bing APK not found: {self.bing_apk}"
        return ldplayer_service.install_app(index, self.bing_apk)

    def install_oxyproxy_to_index(self, index: int) -> Tuple[bool, str]:
        if not self.oxyproxy_xapk.exists():
            return False, f"OxyProxy XAPK not found: {self.oxyproxy_xapk}"
        return self.install_xapk_to_index(index, self.oxyproxy_xapk)

    def verify_installation(self, serial: str, package_name: str) -> Dict:
        installed = adb_service.is_package_installed(serial, package_name)
        version = None
        if installed:
            result = adb_service.shell(serial, f"dumpsys package {package_name} | grep versionName")
            if result.returncode == 0:
                import re
                match = re.search(r"versionName=([^\s]+)", result.stdout)
                if match:
                    version = match.group(1)
        return {"installed": installed, "package_name": package_name, "version": version}


installer_service = InstallerService()