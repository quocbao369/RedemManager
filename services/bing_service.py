from dataclasses import dataclass
from typing import Optional
from config import settings
from services.adb_service import adb_service
from services.installer_service import installer_service
from services.proxy_service import proxy_service
from database import log_activity


@dataclass
class BingLaunchResult:
    success: bool
    message: str
    package_verified: bool = False
    proxy_verified: bool = False


class BingService:
    def __init__(self):
        self.package_name = settings.apps.bing_package_name

    def prepare_bing(self, serial: str, proxy_profile_id: Optional[int] = None) -> BingLaunchResult:
        log_activity("INFO", f"Preparing Bing on {serial}", instance_id=None)

        installed = adb_service.is_package_installed(serial, self.package_name)
        if not installed:
            success, msg = installer_service.install_bing(serial)
            if not success:
                return BingLaunchResult(success=False, message=f"Bing install failed: {msg}")
            log_activity("INFO", "Bing installed successfully", instance_id=None)

        if proxy_profile_id:
            profile = proxy_service.get_profile(proxy_profile_id)
            if profile:
                if proxy_service.configure_device_proxy(serial, profile):
                    log_activity("INFO", f"Proxy configured: {profile.host}:{profile.port}", instance_id=None)
                else:
                    log_activity("WARN", "Failed to configure proxy on device", instance_id=None)

        return BingLaunchResult(success=True, message="Bing ready", package_verified=True)

    def launch_bing(self, serial: str) -> BingLaunchResult:
        log_activity("INFO", f"Launching Bing on {serial}", instance_id=None)
        success = adb_service.launch_package(serial, self.package_name)
        if not success:
            return BingLaunchResult(success=False, message="Failed to launch Bing")
        return BingLaunchResult(success=True, message="Bing launched", package_verified=True)

    def wait_for_manual_login(self, serial: str, timeout: int = 300) -> BingLaunchResult:
        import time
        start = time.time()
        log_activity("INFO", "Waiting for manual login...", instance_id=None)

        while time.time() - start < timeout:
            time.sleep(5)
            result = adb_service.shell(serial, f"dumpsys activity activities | grep {self.package_name}")
            if result.returncode == 0 and "ResumedActivity" in result.stdout:
                return BingLaunchResult(success=True, message="Login detected (app in foreground)")

        return BingLaunchResult(success=False, message="Login timeout")

    def verify_bing_running(self, serial: str) -> bool:
        result = adb_service.shell(serial, f"dumpsys activity activities | grep {self.package_name}")
        return result.returncode == 0 and "ResumedActivity" in result.stdout

    def get_bing_version(self, serial: str) -> Optional[str]:
        result = adb_service.shell(serial, f"dumpsys package {self.package_name} | grep versionName")
        if result.returncode == 0:
            import re
            match = re.search(r"versionName=([^\s]+)", result.stdout)
            if match:
                return match.group(1)
        return None


bing_service = BingService()