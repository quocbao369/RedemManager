import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.ldplayer_service import LDPlayerService


@pytest.fixture
def svc():
    return LDPlayerService()


# ------------------------------------------------------------- short name
@pytest.mark.parametrize("raw,expected", [
    ("vilejuhad278@gmail.com", "vilejuhad278"),
    ("someone@yahoo.com", "someone"),
    ("user@outlook.com", "user"),
    ("LDPlayer-3", "LDPlayer-3"),          # khong phai email -> giu nguyen
    ("  spaced@mail.com  ", "spaced"),     # cat khoang trang thua
    ("a@b", "a"),
    ("", ""),
    (None, None),
])
def test_short_name(raw, expected):
    assert LDPlayerService.short_name(raw) == expected


def test_create_instance_shortens_email(svc):
    """Ten instance tao tu email phai bo phan @domain."""
    with patch.object(svc, "list_instances",
                      side_effect=[[type("I", (), {"index": 0})()],
                                   [type("I", (), {"index": 0})(),
                                    type("I", (), {"index": 1})()]]), \
            patch.object(svc, "_finalize_new_instance", return_value=9), \
            patch.object(svc, "_run_cmd",
                         return_value=type("R", (), {"stdout": "", "returncode": 0,
                                                     "stderr": ""})()) as run:
        svc.create_instance("vilejuhad278@gmail.com")

    args = run.call_args[0][0]
    assert args[:2] == ["add", "--name"]
    assert args[2] == "vilejuhad278"


def test_clone_instance_shortens_email(svc):
    i0 = type("I", (), {"index": 0})()
    with patch.object(svc, "list_instances",
                      side_effect=[[i0], [i0], [i0, type("I", (), {"index": 1})()]]), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=3), \
            patch.object(svc, "_run_cmd",
                         return_value=type("R", (), {"stdout": "", "returncode": 0,
                                                     "stderr": ""})()) as run:
        svc.clone_instance(0, "newguy@gmail.com")

    args = run.call_args[0][0]
    assert args == ["copy", "--name", "newguy", "--from", "0"]


# ------------------------------------------------------- vietnamese locale
class _R:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_adb(props=None, settings=None, uid="0"):
    """Stand-in for the adb_service module used by set_vietnamese_language."""
    class FakeAdb:
        def __init__(self):
            self.shell_cmds = []

        def ensure_device(self, serial, timeout=None):
            return True

        def get_setting(self, serial, namespace, key):
            return (settings or {}).get(key, "")

        def getprop(self, serial, prop):
            return (props or {}).get(prop, "")

        def get_state(self, serial):
            return "device"

        def shell(self, serial, command):
            self.shell_cmds.append(command)
            if command == "id -u":
                return _R(stdout=uid)
            return _R()

    return FakeAdb()


def test_set_vietnamese_skips_when_already_vietnamese(svc):
    """system_locales da chua 'vi' -> khong can lam gi ca."""
    adb = _fake_adb(settings={"system_locales": "vi-VN"})
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service", adb):
        assert svc.set_vietnamese_language(0) is True
    assert adb.shell_cmds == []


def test_set_vietnamese_shortcut_when_old_android_prop_set(svc):
    """Android < 13 doc persist.sys.* khi boot: prop dung roi = hieu luc."""
    adb = _fake_adb(props={"persist.sys.locale": "vi-VN",
                           "ro.build.version.sdk": "28"})
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service", adb):
        assert svc.set_vietnamese_language(0) is True
    assert adb.shell_cmds == []


def test_set_vietnamese_android14_writes_setting_and_reboots(svc):
    """Android 14 (khong root): ghi system_locales + reboot, KHONG stop/start."""
    adb = _fake_adb(props={"persist.sys.locale": "en-US",
                           "ro.build.version.sdk": "34"}, uid="2000")
    with patch.object(svc, "get_serial", return_value="emulator-5556"), \
            patch.object(svc, "_wait_boot_completed", return_value=True) as wait, \
            patch("services.ldplayer_service.adb_service", adb):
        assert svc.set_vietnamese_language(0) is True
    wait.assert_called_once()
    joined = " | ".join(adb.shell_cmds)
    assert "settings put system system_locales vi-VN" in joined
    assert "setprop persist.sys.locale vi-VN" in joined
    assert "reboot" in adb.shell_cmds
    assert "stop" not in adb.shell_cmds and "start" not in adb.shell_cmds


def test_set_vietnamese_android14_never_skips_on_stale_prop(svc):
    """persist.sys.locale=vi-VN tren Android 14 KHONG du: phai ghi setting."""
    # Day dung trang thai that da bug: prop vi-VN nhung system_locales=null.
    adb = _fake_adb(props={"persist.sys.locale": "vi-VN",
                           "ro.build.version.sdk": "34"}, uid="2000")
    with patch.object(svc, "get_serial", return_value="emulator-5556"), \
            patch.object(svc, "_wait_boot_completed", return_value=True), \
            patch("services.ldplayer_service.adb_service", adb):
        assert svc.set_vietnamese_language(0) is True
    assert "settings put system system_locales vi-VN" in adb.shell_cmds


def test_set_vietnamese_android9_root_restarts_framework(svc):
    adb = _fake_adb(props={"persist.sys.locale": "en-US",
                           "ro.build.version.sdk": "28"}, uid="0")
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service", adb), \
            patch("services.ldplayer_service.time.sleep"):
        assert svc.set_vietnamese_language(0) is True
    assert "stop" in adb.shell_cmds and "start" in adb.shell_cmds
    assert "reboot" not in adb.shell_cmds
    assert "setprop persist.sys.locale vi-VN" in " | ".join(adb.shell_cmds)


def test_set_vietnamese_skips_restart_when_not_requested(svc):
    adb = _fake_adb(props={"ro.build.version.sdk": "28"})
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service", adb):
        assert svc.set_vietnamese_language(0, restart_framework=False) is True
    assert "stop" not in adb.shell_cmds and "start" not in adb.shell_cmds
    assert "reboot" not in adb.shell_cmds
    assert "settings put system system_locales vi-VN" in " | ".join(adb.shell_cmds)


def test_set_vietnamese_fails_without_device(svc):
    adb = _fake_adb()
    adb.ensure_device = lambda serial, timeout=None: False
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service", adb):
        assert svc.set_vietnamese_language(0) is False
    assert adb.shell_cmds == []


def test_wait_boot_completed_polls_until_flag(svc):
    with patch("services.ldplayer_service.adb_service") as adb, \
            patch("services.ldplayer_service.time.sleep") as sleep:
        adb.get_state.side_effect = ["", "offline", "device"]
        adb.shell.return_value = _R(stdout="1")
        assert svc._wait_boot_completed("emulator-5554", timeout=120) is True
        assert sleep.call_count >= 2


def test_wait_boot_completed_times_out(svc):
    with patch("services.ldplayer_service.adb_service") as adb, \
            patch("services.ldplayer_service.time.sleep"):
        adb.get_state.return_value = ""
        assert svc._wait_boot_completed("emulator-5554", timeout=0) is False