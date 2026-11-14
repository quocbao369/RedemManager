import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


@dataclass
class LDPlayerConfig:
    ldplayer_path: Path = field(default_factory=lambda: Path(os.getenv("LDPLAYER_PATH", r"C:\ProgramFiles\LDPlayer")))
    ldconsole_path: Path = field(default_factory=lambda: Path(os.getenv("LDPLAYER_PATH", r"C:\ProgramFiles\LDPlayer")) / "ldconsole.exe")
    adb_path: Path = field(default_factory=lambda: Path(os.getenv("ADB_PATH") or ((Path(os.getenv("LDPLAYER_PATH", r"C:\ProgramFiles\LDPlayer"))) / "adb.exe")))
    default_timeout: int = int(os.getenv("DEFAULT_TIMEOUT", "120"))
    boot_timeout: int = int(os.getenv("BOOT_TIMEOUT", "300"))
    adb_connect_timeout: int = int(os.getenv("ADB_CONNECT_TIMEOUT", "60"))

    def __post_init__(self):
        self.ldplayer_path = Path(self.ldplayer_path)
        self.ldconsole_path = Path(self.ldconsole_path)
        self.adb_path = Path(self.adb_path)


@dataclass
class LDPlayer14Config:
    """Optional second LDPlayer installation (LDPlayer 14).

    Each install owns its own ldconsole.exe and its own set of instances
    with 0-based indices, so instance index 3 can exist in BOTH installs.
    The dashboard defaults to version 9; version 14 is only drivable once
    its install folder is configured (Settings > Paths, or LDPLAYER14_PATH).
    """
    ldplayer_path: Optional[Path] = None
    ldconsole_path: Optional[Path] = None
    adb_path: Optional[Path] = None

    def __post_init__(self):
        # Environment fallback so the folder can also be set without the UI.
        env = (os.getenv("LDPLAYER14_PATH") or "").strip()
        if env and self.ldplayer_path is None:
            self.ldplayer_path = Path(env)
        if self.ldplayer_path is not None:
            self.ldplayer_path = Path(self.ldplayer_path)
            if self.ldconsole_path is None:
                self.ldconsole_path = self.ldplayer_path / "ldconsole.exe"
            if self.adb_path is None:
                self.adb_path = self.ldplayer_path / "adb.exe"
        if self.ldconsole_path is not None:
            self.ldconsole_path = Path(self.ldconsole_path)
        if self.adb_path is not None:
            self.adb_path = Path(self.adb_path)

    def is_configured(self) -> bool:
        """True only when ldconsole.exe actually exists on disk."""
        return bool(self.ldconsole_path and self.ldconsole_path.exists())


@dataclass
class AppConfig:
    bing_apk_path: Path = field(default_factory=lambda: Path(os.getenv("BING_APK_PATH", r"D:\AccMicro\MicrosoftBing_33.5.440623004_apkcombo.com.apk")))
    oxyproxy_xapk_path: Path = field(default_factory=lambda: Path(os.getenv("OXYPROXY_XAPK_PATH", r"D:\AccMicro\OxyProxy.xapk")))
    bing_package_name: str = os.getenv("BING_PACKAGE_NAME", "com.microsoft.bing")
    oxyproxy_package_name: str = os.getenv("OXYPROXY_PACKAGE_NAME", "com.oxyproxy.app")
    bing_order_history_url: str = os.getenv("BING_ORDER_HISTORY_URL", "https://rewards.bing.com/redeem/orderhistory")
    connectivity_test_url: str = os.getenv("CONNECTIVITY_TEST_URL", "http://www.gstatic.com/generate_204")

    def __post_init__(self):
        self.bing_apk_path = Path(self.bing_apk_path)
        self.oxyproxy_xapk_path = Path(self.oxyproxy_xapk_path)


@dataclass
class ServerConfig:
    host: str = os.getenv("SERVER_HOST", "127.0.0.1")
    port: int = int(os.getenv("SERVER_PORT", "8765"))
    reload: bool = os.getenv("SERVER_RELOAD", "false").lower() == "true"


@dataclass
class DatabaseConfig:
    path: Path = field(default_factory=lambda: Path(os.getenv("DATABASE_PATH") or (DATA_DIR / "redem.db")))
    echo: bool = os.getenv("DB_ECHO", "false").lower() == "true"


@dataclass
class LoggingConfig:
    level: str = os.getenv("LOG_LEVEL", "INFO")
    file: Path = field(default_factory=lambda: LOGS_DIR / "redem.log")
    max_bytes: int = int(os.getenv("LOG_MAX_BYTES", "10485760"))
    backup_count: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))


@dataclass
class RewardsConfig:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("REWARDS_DB_PATH", r"D:\AccMicro\TOOL\VIP\rewards_data.db")))

    def __post_init__(self):
        self.db_path = Path(self.db_path)


@dataclass
class Settings:
    ldplayer: LDPlayerConfig = field(default_factory=LDPlayerConfig)
    ldplayer14: LDPlayer14Config = field(default_factory=LDPlayer14Config)
    apps: AppConfig = field(default_factory=AppConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    rewards: RewardsConfig = field(default_factory=RewardsConfig)
    workflow_lock_timeout: int = int(os.getenv("WORKFLOW_LOCK_TIMEOUT", "3600"))


settings = Settings()

ENV_FILE = BASE_DIR / ".env"


def _write_env(updates: dict) -> None:
    """Put the new values into .env so they survive a server restart.

    Existing keys are replaced in place and unknown lines (comments, other
    settings) are left untouched.
    """
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_settings_update(values: dict) -> list:
    """Apply saved values to the running config and to .env.

    `settings` is built once at import time, so only writing to os.environ
    left every later read pointing at the old value: the Settings form
    reported "saved" and then reloaded the previous path. This mutates the
    same object the rest of the app already imported.

    Returns human-readable warnings for saved values that were skipped.
    """
    env_updates: dict = {}
    warnings: list = []

    if values.get("ldplayer_path"):
        folder = Path(values["ldplayer_path"])
        # Guard: the LDPlayer 14 folder belongs in its own field. Saving it as
        # the main path reroutes every LDPlayer 9 machine to an installation
        # that owns none of those instance indexes, so most launches then find
        # no emulator at all and apps never open.
        ld14_same = False
        try:
            ld14_same = (settings.ldplayer14.is_configured()
                         and folder.resolve() == Path(settings.ldplayer14.ldplayer_path).resolve())
        except Exception:
            ld14_same = False
        if ld14_same:
            warnings.append(
                "Bo qua 'LDPlayer Directory' vi no trung voi thu muc LDPlayer 14. "
                "LD9 giu o 'LDPlayer Directory'; LD14 chi dung o 'LDPlayer 14 Directory'.")
        else:
            settings.ldplayer.ldplayer_path = folder
            settings.ldplayer.ldconsole_path = folder / "ldconsole.exe"
            env_updates["LDPLAYER_PATH"] = str(folder)
            # ADB normally lives next to ldconsole, unless it is set explicitly.
            if not values.get("adb_path"):
                settings.ldplayer.adb_path = folder / "adb.exe"
                env_updates["ADB_PATH"] = str(settings.ldplayer.adb_path)

    if values.get("adb_path"):
        settings.ldplayer.adb_path = Path(values["adb_path"])
        env_updates["ADB_PATH"] = str(settings.ldplayer.adb_path)

    # LDPlayer 14 (optional): a filled folder enables it, an empty field
    # disables it again. The key being absent (None) leaves things untouched.
    if "ldplayer14_path" in values and values.get("ldplayer14_path") is not None:
        raw = str(values["ldplayer14_path"]).strip()
        if raw:
            folder = Path(raw)
            settings.ldplayer14.ldplayer_path = folder
            settings.ldplayer14.ldconsole_path = folder / "ldconsole.exe"
            settings.ldplayer14.adb_path = folder / "adb.exe"
            env_updates["LDPLAYER14_PATH"] = str(folder)
        else:
            settings.ldplayer14.ldplayer_path = None
            settings.ldplayer14.ldconsole_path = None
            settings.ldplayer14.adb_path = None
            env_updates["LDPLAYER14_PATH"] = ""

    if values.get("bing_apk_path"):
        settings.apps.bing_apk_path = Path(values["bing_apk_path"])
        env_updates["BING_APK_PATH"] = str(settings.apps.bing_apk_path)

    if values.get("oxyproxy_xapk_path"):
        settings.apps.oxyproxy_xapk_path = Path(values["oxyproxy_xapk_path"])
        env_updates["OXYPROXY_XAPK_PATH"] = str(settings.apps.oxyproxy_xapk_path)

    if values.get("default_timeout"):
        settings.ldplayer.default_timeout = int(values["default_timeout"])
        env_updates["DEFAULT_TIMEOUT"] = str(values["default_timeout"])

    if values.get("boot_timeout"):
        settings.ldplayer.boot_timeout = int(values["boot_timeout"])
        env_updates["BOOT_TIMEOUT"] = str(values["boot_timeout"])

    if values.get("server_port"):
        settings.server.port = int(values["server_port"])
        env_updates["SERVER_PORT"] = str(values["server_port"])

    if values.get("server_host"):
        settings.server.host = values["server_host"]
        env_updates["SERVER_HOST"] = values["server_host"]

    if values.get("bing_package_name"):
        settings.apps.bing_package_name = values["bing_package_name"]
        env_updates["BING_PACKAGE_NAME"] = values["bing_package_name"]

    if values.get("oxyproxy_package_name"):
        settings.apps.oxyproxy_package_name = values["oxyproxy_package_name"]
        env_updates["OXYPROXY_PACKAGE_NAME"] = values["oxyproxy_package_name"]

    if values.get("bing_order_history_url"):
        settings.apps.bing_order_history_url = values["bing_order_history_url"]
        env_updates["BING_ORDER_HISTORY_URL"] = values["bing_order_history_url"]

    if values.get("connectivity_test_url"):
        settings.apps.connectivity_test_url = values["connectivity_test_url"]
        env_updates["CONNECTIVITY_TEST_URL"] = values["connectivity_test_url"]

    if values.get("database_path"):
        settings.database.path = Path(values["database_path"])
        env_updates["DATABASE_PATH"] = str(settings.database.path)

    if values.get("rewards_db_path"):
        settings.rewards.db_path = Path(values["rewards_db_path"])
        env_updates["REWARDS_DB_PATH"] = str(settings.rewards.db_path)

    if values.get("log_level"):
        settings.logging.level = values["log_level"]
        env_updates["LOG_LEVEL"] = values["log_level"]

    if values.get("workflow_lock_timeout"):
        settings.workflow_lock_timeout = int(values["workflow_lock_timeout"])
        env_updates["WORKFLOW_LOCK_TIMEOUT"] = str(values["workflow_lock_timeout"])

    for key, value in env_updates.items():
        os.environ[key] = value
    if env_updates:
        _write_env(env_updates)
    return warnings


def validate_paths() -> list[str]:
    errors = []
    if not settings.ldplayer.ldconsole_path.exists():
        errors.append(f"ldconsole not found: {settings.ldplayer.ldconsole_path}")
    if not settings.ldplayer.adb_path.exists():
        errors.append(f"adb not found: {settings.ldplayer.adb_path}")
    # LDPlayer 14 is validated only when a folder was configured: an unset
    # install is a valid state ("khong dung LD14"), a wrong folder is not.
    if settings.ldplayer14.ldconsole_path and not settings.ldplayer14.ldconsole_path.exists():
        errors.append(f"LDPlayer 14 ldconsole not found: {settings.ldplayer14.ldconsole_path}")
    if not settings.apps.bing_apk_path.exists():
        errors.append(f"Bing APK not found: {settings.apps.bing_apk_path}")
    if not settings.apps.oxyproxy_xapk_path.exists():
        errors.append(f"OxyProxy XAPK not found: {settings.apps.oxyproxy_xapk_path}")
    return errors