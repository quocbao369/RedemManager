import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.ldplayer_service import LDPlayerService


@pytest.fixture
def svc():
    return LDPlayerService()


def _ok():
    return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()


def test_remove_stopped_instance_goes_straight_to_remove(svc):
    with patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "quit_instance") as quit_, \
            patch.object(svc, "_run_cmd", return_value=_ok()) as run:
        assert svc.remove_instance(2) is True

    quit_.assert_not_called()
    assert run.call_args[0][0] == ["remove", "--index", "2"]


def test_remove_running_instance_stops_it_first(svc):
    with patch.object(svc, "is_running", return_value=True), \
            patch.object(svc, "quit_instance", return_value=True) as quit_, \
            patch.object(svc, "_wait_stopped", return_value=True) as waited, \
            patch.object(svc, "_run_cmd", return_value=_ok()) as run:
        assert svc.remove_instance(2) is True

    quit_.assert_called_once_with(2)
    waited.assert_called_once_with(2, timeout=180)
    assert run.call_args[0][0] == ["remove", "--index", "2"]


def test_remove_refuses_when_instance_will_not_stop(svc):
    """Khong duoc xoa LDPlayer dang chay - se bo lai o dia."""
    with patch.object(svc, "is_running", return_value=True), \
            patch.object(svc, "quit_instance", return_value=True), \
            patch.object(svc, "_wait_stopped", return_value=False), \
            patch.object(svc, "_run_cmd") as run:
        with pytest.raises(RuntimeError):
            svc.remove_instance(2)
    run.assert_not_called()


def test_remove_raises_on_ldconsole_error(svc):
    with patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_run_cmd",
                         return_value=type("R", (), {"stdout": "", "returncode": 1,
                                                     "stderr": "busy"})()):
        with pytest.raises(RuntimeError):
            svc.remove_instance(2)


def test_remove_has_generous_timeout(svc):
    """Xoa may emu kem theo dia nen can timeout lon."""
    with patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_run_cmd", return_value=_ok()) as run:
        svc.remove_instance(0)
    assert run.call_args.kwargs.get("timeout", 0) >= 600