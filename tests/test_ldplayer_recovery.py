import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.ldplayer_service import LDPlayerService


@pytest.fixture
def svc():
    return LDPlayerService()


# ------------------------------------------------------------- is_running
def test_is_running_reads_running_word(svc):
    """Regression: ldconsole prints 'running'/'stop', never 1/0."""
    with patch.object(svc, "_run_cmd") as run:
        run.return_value = type("R", (), {"stdout": "running", "returncode": 0})()
        assert svc.is_running(3) is True
        assert "isrunning" in run.call_args[0][0]


def test_is_running_reads_stop_word(svc):
    with patch.object(svc, "_run_cmd") as run:
        run.return_value = type("R", (), {"stdout": "stop", "returncode": 0})()
        assert svc.is_running(3) is False


def test_is_running_handles_hang(svc):
    with patch.object(svc, "_run_cmd", side_effect=RuntimeError("timeout")):
        assert svc.is_running(3) is False


# ----------------------------------------------------------------- get_pids
def _row(index, dn, vbox):
    return f"{index},LDPlayer-{index},0,0,1,{dn},{vbox},720,1280,320"


def test_get_pids_running_instance(svc):
    with patch.object(svc, "_run_cmd") as run:
        run.return_value = type("R", (), {
            "stdout": "\n".join([_row(0, 0, 0), _row(1, 24132, 20660)]),
            "returncode": 0})()
        assert svc.get_pids(1) == (24132, 20660)


def test_get_pids_stopped_instance_returns_none(svc):
    with patch.object(svc, "_run_cmd") as run:
        run.return_value = type("R", (), {
            "stdout": "1,LDPlayer-1,0,0,0,-1,-1,720,1280,320",
            "returncode": 0})()
        assert svc.get_pids(1) == (None, None)


def test_get_pids_missing_index(svc):
    with patch.object(svc, "_run_cmd") as run:
        run.return_value = type("R", (), {
            "stdout": _row(2, 11, 22), "returncode": 0})()
        assert svc.get_pids(9) is None


def test_get_pids_handles_hang(svc):
    with patch.object(svc, "_run_cmd", side_effect=RuntimeError("timeout")):
        assert svc.get_pids(1) is None


# -------------------------------------------------------------- force_stop
def test_force_stop_early_exit_when_already_stopped(svc):
    """No 8s wait and no kill when the instance is already down."""
    with patch.object(svc, "_run_cmd") as run, \
         patch.object(svc, "is_running", return_value=False), \
         patch.object(svc, "_kill_pid") as kill:
        assert svc.force_stop(1) is True
        kill.assert_not_called()


def test_force_stop_kills_own_pids_when_hung(svc):
    """A hung emulator must be killed by PID (quit does not answer)."""
    with patch.object(svc, "_run_cmd", side_effect=RuntimeError("timeout")), \
         patch.object(svc, "is_running", return_value=True), \
         patch.object(svc, "get_pids", return_value=(111, 222)), \
         patch.object(svc, "_kill_pid", return_value=True) as kill, \
         patch("services.ldplayer_service.time.sleep"):
        svc.force_stop(1)
        assert kill.call_count == 2
        assert {c[0][0] for c in kill.call_args_list} == {111, 222}


def test_force_stop_never_touches_pids_when_none(svc):
    with patch.object(svc, "_run_cmd", side_effect=RuntimeError("x")), \
         patch.object(svc, "is_running", return_value=True), \
         patch.object(svc, "get_pids", return_value=None), \
         patch.object(svc, "_kill_pid") as kill, \
         patch("services.ldplayer_service.time.sleep"):
        svc.force_stop(1)
        kill.assert_not_called()


# ------------------------------------------------------------- kill helper
def test_kill_pid_ignores_none():
    assert LDPlayerService._kill_pid(None) is False


def test_kill_pid_invokes_taskkill():
    with patch("services.ldplayer_service.subprocess.run") as run:
        run.return_value = type("R", (), {"returncode": 0})()
        assert LDPlayerService._kill_pid(1234) is True
        args = run.call_args[0][0]
        assert args[0] == "taskkill"
        assert "1234" in args


def test_kill_pid_handles_failure():
    with patch("services.ldplayer_service.subprocess.run") as run:
        run.return_value = type("R", (), {"returncode": 1})()
        assert LDPlayerService._kill_pid(1234) is False


def test_kill_pid_handles_exception():
    with patch("services.ldplayer_service.subprocess.run",
               side_effect=OSError("denied")):
        assert LDPlayerService._kill_pid(1234) is False
