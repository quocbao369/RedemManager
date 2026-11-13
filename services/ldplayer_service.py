import subprocess
import time
import re
import json
import random
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from config import settings
from database import Database, dict_from_row, log_activity
from services.adb_service import adb_service


@dataclass
class InstanceInfo:
    index: int
    name: str
    status: str
    serial: Optional[str] = None
    android_version: Optional[str] = None
    resolution: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_mb: Optional[int] = None


class LDPlayerService:
    # Live reads from config so a path saved in Settings takes effect without
    # restarting the server.
    @property
    def ldconsole(self) -> Path:
        return settings.ldplayer.ldconsole_path

    @property
    def adb(self) -> Path:
        return settings.ldplayer.adb_path

    @property
    def default_timeout(self) -> int:
        return settings.ldplayer.default_timeout

    def _run_cmd(self, args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        if timeout is None:
            timeout = self.default_timeout
        try:
            result = subprocess.run(
                [str(self.ldconsole)] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            return result
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(args)}") from e
        except FileNotFoundError:
            raise RuntimeError(f"ldconsole not found at {self.ldconsole}")

    def _run_adb(self, args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        if timeout is None:
            timeout = settings.ldplayer.adb_connect_timeout
        try:
            result = subprocess.run(
                [str(self.adb)] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            return result
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ADB command timed out after {timeout}s: {' '.join(args)}") from e
        except FileNotFoundError:
            raise RuntimeError(f"adb not found at {self.adb}")

    def list_instances(self) -> List[InstanceInfo]:
        result = self._run_cmd(["list2"])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list instances: {result.stderr}")

        instances = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 10:
                index = int(parts[0])
                name = parts[1]
                # Running status is in parts[4] (1 = running, 0 = stopped)
                is_running = parts[4] == "1"
                status = "running" if is_running else "stopped"
                instances.append(InstanceInfo(index=index, name=name, status=status))
        return instances

    def get_instance_detail(self, index: int) -> Optional[InstanceInfo]:
        """Read an instance's detail from `list2`.

        NOTE: LDPlayer 9's `list3` field 2 is NOT the started/running flag - on a
        fully booted emulator it still reports 0, which previously made
        wait_for_boot() spin until it timed out. `list2` field 4 (androidstarted)
        is the reliable value.
        """
        result = self._run_cmd(["list2"])
        if result.returncode != 0:
            return None

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) < 10:
                continue
            if int(parts[0]) != index:
                continue
            return InstanceInfo(
                index=int(parts[0]),
                name=parts[1],
                status="running" if parts[4] == "1" else "stopped",
                serial=self.get_serial(index),
                resolution=f"{parts[7]}x{parts[8]}" if parts[7] and parts[8] else None,
            )
        return None

    def get_ld_index(self, db_id: int) -> Optional[int]:
        """Get LD index from database ID."""
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT ld_index FROM instances WHERE id = ?", (db_id,))
            row = cursor.fetchone()
            return row["ld_index"] if row else None

    def _finalize_new_instance(self, ld_index: int, phone_mode: bool) -> int:
        """Apply settings to a freshly created instance and return its DB id."""
        if phone_mode:
            self.configure_phone_mode(ld_index)
        # ADB debugging is required for installing apps / setting the proxy.
        self.enable_adb_debug(ld_index)

        self.sync_instances_to_db()
        for inst in self.get_db_instances():
            if inst["ld_index"] == ld_index:
                return inst["id"]
        return ld_index  # fallback

    @staticmethod
    def short_name(name: Optional[str]) -> Optional[str]:
        """Turn an email into a short instance name.

        LDPlayer shows the title in a narrow tab, so "user123@gmail.com"
        becomes "user123". Names without an @ are returned untouched.
        """
        if not name:
            return name
        cleaned = name.strip()
        return re.sub(r"@.*$", "", cleaned) or cleaned

    def create_instance(self, name: Optional[str] = None, phone_mode: bool = True) -> int:
        name = self.short_name(name)
        args = ["add"]
        if name:
            args.extend(["--name", name])
        before = set(i.index for i in self.list_instances())
        result = self._run_cmd(args)
        after = set(i.index for i in self.list_instances())
        new_indices = after - before
        if new_indices:
            return self._finalize_new_instance(max(new_indices), phone_mode)

        output = result.stdout.strip()
        match = re.search(r"index[:\s]+(\d+)", output, re.IGNORECASE)
        if match:
            return self._finalize_new_instance(int(match.group(1)), phone_mode)

        instances = self.list_instances()
        if instances:
            return self._finalize_new_instance(max(i.index for i in instances), phone_mode)
        raise RuntimeError("Instance created but could not determine index")

    def clone_instance(self, source_index: int, name: Optional[str] = None,
                       phone_mode: bool = True) -> int:
        """Copy an existing instance (LDPlayer "copy") and return its DB id.

        The source instance is left untouched. LDPlayer refuses to clone a
        running instance, so it is stopped first and started again afterwards
        when the caller had it running.
        """
        if source_index not in set(i.index for i in self.list_instances()):
            raise ValueError(f"Instance nguon {source_index} khong ton tai")

        was_running = self.is_running(source_index)
        if was_running:
            self.quit_instance(source_index)
            self._wait_stopped(source_index, timeout=180)

        name = self.short_name(name)
        before = set(i.index for i in self.list_instances())
        # ldconsole only accepts "--name" before "--from"; the reverse order
        # makes it print the usage screen and fail with -1000.
        args = ["copy"]
        if name:
            args.extend(["--name", name])
        args.extend(["--from", str(source_index)])
        result = self._run_cmd(args, timeout=1800)

        after = set(i.index for i in self.list_instances())
        new_indices = after - before

        if was_running:
            self.launch_instance(source_index, wait_for_boot=False)

        if new_indices:
            return self._finalize_new_instance(max(new_indices), phone_mode)

        # ldconsole returns the new index as its exit code on success, and a
        # negative code (e.g. -1000) when it rejected the arguments. Index 0
        # can never be new because it is the source we copied from.
        code = result.returncode
        if isinstance(code, int) and code >= 0:
            return self._finalize_new_instance(code, phone_mode)

        match = re.search(r"index[:\s]+(\d+)", result.stdout.strip(), re.IGNORECASE)
        if match:
            return self._finalize_new_instance(int(match.group(1)), phone_mode)
        raise RuntimeError(
            "Da copy instance nhung khong xac dinh duoc index moi: "
            f"{result.stderr.strip() or result.stdout.strip()}")

    def rename_instance(self, index: int, new_name: str) -> bool:
        result = self._run_cmd(["rename", "--index", str(index), "--title", new_name])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to rename instance {index}: {result.stderr}")
        return True

    # Realistic phone models for randomization
    PHONE_MODELS = [
        ("google", "Pixel 7"),
        ("google", "Pixel 7 Pro"),
        ("google", "Pixel 6a"),
        ("google", "Pixel 6"),
        ("samsung", "SM-S918B"),
        ("samsung", "SM-S911B"),
        ("samsung", "SM-G998B"),
        ("samsung", "SM-G991B"),
        ("xiaomi", "23049RAD8C"),
        ("xiaomi", "2211133G"),
        ("oneplus", "CPH2451"),
        ("oneplus", "CPH2413"),
        ("motorola", "XT2321-1"),
        ("sony", "XQ-ES72"),
    ]

    def _instance_config_path(self, index: int) -> Path:
        """Path to LDPlayer's per-instance config file (leidian<N>.config)."""
        return Path(self.ldconsole).parent / "vms" / "config" / f"leidian{index}.config"

    def enable_adb_debug(self, index: int) -> bool:
        """Turn on ADB debugging for an instance.

        LDPlayer has no CLI switch for this, but the emulator reads
        "basicSettings.adbDebug" from its config file. The instance must be
        stopped, otherwise LDPlayer overwrites the file on shutdown.
        """
        config_path = self._instance_config_path(index)
        if not config_path.exists():
            return False

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        if data.get("basicSettings.adbDebug") == 1:
            return True

        data["basicSettings.adbDebug"] = 1
        config_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
        return True

    def set_vietnamese_language(self, index: int, restart_framework: bool = True) -> bool:
        """Switch the Android system locale of a running instance to vi-VN.

        LDPlayer only stores its own UI language (leidians.config); the guest
        locale lives in the system properties, so it has to be pushed over
        ADB. Changing it needs a framework restart to take effect.
        """
        serial = self.get_serial(index)
        if not serial or not adb_service.ensure_device(serial, timeout=60):
            return False

        for prop, value in (("persist.sys.locale", "vi-VN"),
                            ("persist.sys.language", "vi"),
                            ("persist.sys.country", "VN")):
            adb_service.shell(serial, "setprop %s %s" % (prop, value))

        if not restart_framework:
            return True

        adb_service.shell(serial, "stop")
        time.sleep(5)
        adb_service.shell(serial, "start")
        # The framework restarts; wait for Android to accept ADB again.
        return adb_service.ensure_device(serial, timeout=180)

    def configure_phone_mode(self, index: int) -> bool:
        # Compact portrait phone: 480x854 @ 240dpi, 2 CPU, 2048 MB
        manufacturer, model = random.choice(self.PHONE_MODELS)
        result = self._run_cmd([
            "modify", "--index", str(index),
            "--resolution", "480,854,240",
            "--cpu", "4",
            "--memory", "4096",
            "--manufacturer", manufacturer,
            "--model", model,
            "--autorotate", "0",
            "--lockwindow", "1"
        ])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to configure phone mode for instance {index}: {result.stderr}")
        return True

    def rename_instance(self, index: int, new_name: str) -> bool:
        result = self._run_cmd(["rename", "--index", str(index), "--title", new_name])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to rename instance {index}: {result.stderr}")
        return True

    def launch_instance(self, index: int, wait_for_boot: bool = True) -> bool:
        try:
            result = self._run_cmd(["launch", "--index", str(index)])
            if result.returncode != 0:
                raise RuntimeError(f"Failed to launch instance {index}: {result.stderr}")
        except Exception as e:
            # A frozen emulator refuses `launch`; recover it and try again.
            log_activity("WARN", f"Launch failed ({e}), restarting instance {index}")
            if not self.restart_instance(index, wait_for_boot=wait_for_boot):
                raise RuntimeError(f"Failed to launch instance {index}: {e}")

        if wait_for_boot:
            return self.wait_for_boot(index)
        return True

    def wait_for_boot(self, index: int, timeout: Optional[int] = None,
                      allow_restart: bool = True) -> bool:
        """Wait until Android has fully booted, verified through ADB.

        On timeout the instance is force-restarted once, because a stuck boot
        is almost always recoverable.
        """
        if timeout is None:
            timeout = settings.ldplayer.boot_timeout
        serial = self.get_serial(index)
        deadline = time.time() + timeout

        while time.time() < deadline:
            detail = self.get_instance_detail(index)
            if detail and detail.status == "running":
                if adb_service.ensure_device(serial, timeout=30):
                    boot = adb_service.shell(serial, "getprop sys.boot_completed")
                    if boot.returncode == 0 and boot.stdout.strip() == "1":
                        return True
            time.sleep(3)

        if not allow_restart:
            raise TimeoutError(f"Instance {index} did not boot within {timeout}s")

        log_activity("WARN",
                     f"Instance {index} boot timeout ({timeout}s) - restarting")
        if self.restart_instance(index, wait_boot=True, attempts=1):
            return True
        raise TimeoutError(f"Instance {index} did not boot after restart")

    def _wait_stopped(self, index: int, timeout: int = 180) -> bool:
        """Wait until LDPlayer reports the instance as fully stopped.

        Cloning copies the disk image, so both the reported status and the
        instance PIDs have to be gone before LDPlayer is allowed to proceed.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            detail = self.get_instance_detail(index)
            if (not detail or detail.status == "stopped") and not self.get_pids(index):
                return True
            time.sleep(3)
        return False

    def get_pids(self, index: int) -> Optional[tuple]:
        """Return (dnplayer_pid, ldvbox_pid) for a running instance.

        LDPlayer 9 list2 layout: index,title,topWin,bindWin,androidStarted,
        dnplayer_pid,ldvbox_pid,width,height,dpi. The shared Ld9BoxSVC
        service is intentionally NOT included so it is never killed.
        """
        try:
            result = self._run_cmd(["list2"], timeout=20)
        except Exception:
            return None
        if result.returncode != 0:
            return None

        for line in result.stdout.strip().split("\n"):
            parts = line.split(",")
            if len(parts) < 10:
                continue
            if not parts[0].strip().isdigit() or int(parts[0]) != index:
                continue
            dn, vbox = parts[5].strip(), parts[6].strip()
            dn_pid = int(dn) if dn.lstrip("-").isdigit() and int(dn) > 0 else None
            vbox_pid = int(vbox) if vbox.lstrip("-").isdigit() and int(vbox) > 0 else None
            return dn_pid, vbox_pid
        return None

    @staticmethod
    def _kill_pid(pid: Optional[int]) -> bool:
        if not pid:
            return False
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    def force_stop(self, index: int) -> bool:
        """Stop an instance, escalating to killing its own processes.

        A hung emulator no longer answers `ldconsole quit`, so the only way out
        is to terminate the two processes that belong to this instance.
        """
        stopped = False
        try:
            result = self._run_cmd(["quit", "--index", str(index)], timeout=30)
            stopped = result.returncode == 0
        except Exception:
            stopped = False

        # Give the graceful path a moment before escalating.
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                if not self.is_running(index):
                    return True
            except Exception:
                break
            time.sleep(2)

        pids = self.get_pids(index)
        if not pids:
            return stopped
        killed = False
        for pid in pids:
            killed = self._kill_pid(pid) or killed
        time.sleep(4)
        return stopped or killed

    def is_responsive(self, index: int) -> bool:
        """Cheap health probe: ldconsole answers and, if running, ADB is online."""
        if not self.is_running(index):
            # A stopped instance is healthy; an exception inside is_running
            # already returned False, so confirm the process really is down.
            try:
                self._run_cmd(["list2"], timeout=20)
                return True
            except Exception:
                return False
        serial = self.get_serial(index)
        try:
            listing = self._run_adb(["devices"], timeout=20)
        except Exception:
            return False
        for line in (listing.stdout or "").splitlines():
            if line.startswith(serial):
                return line.split()[1] == "device"
        return False

    def restart_instance(self, index: int, wait_boot: bool = True,
                         attempts: int = 2) -> bool:
        """Recover a hung instance: force stop, relaunch, wait for boot.

        The ADB server is restarted too, because a frozen emulator usually
        leaves its transport in the "offline" state.
        """
        for attempt in range(1, attempts + 1):
            self.force_stop(index)
            adb_service.restart_server()
            time.sleep(3)

            try:
                self._run_cmd(["launch", "--index", str(index)], timeout=60)
            except Exception:
                continue

            if not wait_boot:
                return True
            try:
                self.wait_for_boot(index)
                return True
            except TimeoutError:
                log_activity("WARN",
                             f"Instance {index} did not boot (attempt {attempt}/{attempts})")
        return False

    def quit_instance(self, index: int) -> bool:
        result = self._run_cmd(["quit", "--index", str(index)])
        if result.returncode != 0:
            raise RuntimeError(f"Failed to quit instance {index}: {result.stderr}")
        return True

    def remove_instance(self, index: int) -> bool:
        """Delete an instance.

        LDPlayer refuses to remove a running emulator, so it is stopped and
        waited out first; deleting a live instance would leave its vmdk behind.
        """
        if self.is_running(index):
            self.quit_instance(index)
            if not self._wait_stopped(index, timeout=180):
                raise RuntimeError(
                    f"Instance {index} did not stop, refusing to delete it")
        result = self._run_cmd(["remove", "--index", str(index)], timeout=900)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to remove instance {index}: {result.stderr}")
        return True

    def is_running(self, index: int) -> bool:
        # LDPlayer prints "running" / "stop", not 1 / 0.
        try:
            result = self._run_cmd(["isrunning", "--index", str(index)], timeout=20)
        except Exception:
            return False
        return result.stdout.strip().lower() == "running"

    def get_serial(self, index: int) -> Optional[str]:
        """Resolve the ADB serial for a specific LDPlayer index.

        LDPlayer exposes instance N on host port (5555 + 2*N), which the ADB
        server reports as "emulator-<port-1>".  Verified on LDPlayer 9.1.63:
        index 1 -> port 5557 -> emulator-5556.

        The previous implementation returned the FIRST serial listed by
        `adb devices`, so every command silently targeted the wrong instance.
        """
        port = 5555 + (2 * index)
        return f"emulator-{port - 1}"

    def install_app(self, index: int, apk_path: Path) -> Tuple[bool, str]:
        """Install a single APK via ADB (verified against real install output)."""
        apk_path = Path(apk_path)
        if not apk_path.exists():
            return False, f"APK not found: {apk_path}"

        serial = self.get_serial(index)
        if not serial:
            return False, f"No ADB serial for instance {index}"

        if not adb_service.ensure_device(serial):
            return False, f"ADB device {serial} is not online"

        ok = adb_service.install_apk(serial, apk_path)
        if ok:
            return True, f"Installed {apk_path.name}"
        return False, f"Failed to install {apk_path.name}"

    def uninstall_app(self, index: int, package_name: str) -> bool:
        result = self._run_cmd(
            ["uninstallapp", "--index", str(index), "--packagename", package_name]
        )
        return result.returncode == 0

    def is_app_installed(self, index: int, package_name: str) -> bool:
        result = self._run_cmd(
            ["operateinfo", "--index", str(index), "--file", "installed_list.txt"]
        )
        return result.returncode == 0

    def launch_app(self, index: int, package_name: str) -> bool:
        result = self._run_cmd(
            ["runapp", "--index", str(index), "--packagename", package_name]
        )
        return result.returncode == 0

    def get_android_version(self, index: int) -> Optional[str]:
        result = self._run_cmd(["getprop", "--index", str(index), "--key", "ro.build.version.release"])
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    def sync_instances_to_db(self):
        instances = self.list_instances()
        with Database.transaction() as conn:
            for inst in instances:
                detail = self.get_instance_detail(inst.index)
                conn.execute(
                    """
                    INSERT INTO instances (ld_index, name, status, serial, android_version, resolution, cpu_cores, memory_mb, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(ld_index) DO UPDATE SET
                        name=excluded.name,
                        status=excluded.status,
                        serial=excluded.serial,
                        android_version=excluded.android_version,
                        resolution=excluded.resolution,
                        cpu_cores=excluded.cpu_cores,
                        memory_mb=excluded.memory_mb,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (inst.index, inst.name, inst.status, self.get_serial(inst.index),
                     detail.android_version if detail else None, detail.resolution if detail else None,
                     detail.cpu_cores if detail else None, detail.memory_mb if detail else None),
                )

    def get_db_instances(self) -> List[Dict[str, Any]]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM instances ORDER BY ld_index")
            return [dict_from_row(row) for row in cursor.fetchall()]


ldplayer_service = LDPlayerService()