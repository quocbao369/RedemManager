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
def test_set_vietnamese_sets_props_and_restarts(svc):
    """Locale duoc dat qua ADB va framework restart de co hieu luc."""
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service") as adb, \
            patch("services.ldplayer_service.time.sleep"):
        adb.ensure_device.return_value = True
        assert svc.set_vietnamese_language(0) is True

    cmds = [c.args[1] for c in adb.shell.call_args_list]
    joined = " | ".join(cmds)
    assert "persist.sys.locale vi-VN" in joined
    assert "persist.sys.language vi" in joined
    assert "persist.sys.country VN" in joined
    assert "stop" in cmds and "start" in cmds


def test_set_vietnamese_skips_restart(svc):
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service") as adb:
        adb.ensure_device.return_value = True
        assert svc.set_vietnamese_language(0, restart_framework=False) is True

    cmds = [c.args[1] for c in adb.shell.call_args_list]
    assert "stop" not in cmds and "start" not in cmds


def test_set_vietnamese_fails_without_device(svc):
    with patch.object(svc, "get_serial", return_value="emulator-5554"), \
            patch("services.ldplayer_service.adb_service") as adb:
        adb.ensure_device.return_value = False
        assert svc.set_vietnamese_language(0) is False