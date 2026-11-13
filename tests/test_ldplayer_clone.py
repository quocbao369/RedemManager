import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.ldplayer_service import LDPlayerService


def _inst(index):
    return type("I", (), {"index": index, "name": "LDPlayer-%s" % index,
                          "status": "stopped"})()


def _ok(stdout="", stderr="", returncode=0):
    return type("R", (), {"stdout": stdout, "stderr": stderr,
                          "returncode": returncode})()


class FakeLD:
    """Mô phỏng ldconsole: sau khi chạy lệnh "copy" sẽ xuất hiện index mới."""

    def __init__(self, indices, copy_stdout="", hide_new=False, copy_rc=None):
        self.indices = list(indices)
        self.copy_stdout = copy_stdout
        self.hide_new = hide_new
        self.copy_rc = copy_rc
        self.commands = []

    def list_instances(self):
        return [_inst(i) for i in self.indices]

    def run_cmd(self, args, timeout=None):
        self.commands.append(list(args))
        if args and args[0] == "copy":
            if not self.hide_new:
                new = max(self.indices) + 1
                self.indices.append(new)
                # ldconsole returns the new index as its exit code
                return _ok(self.copy_stdout,
                           returncode=new if self.copy_rc is None else self.copy_rc)
            return _ok(self.copy_stdout,
                       returncode=self.copy_rc if self.copy_rc is not None else -1)
        return _ok()


@pytest.fixture
def svc():
    return LDPlayerService()


# ------------------------------------------------------------- validation
def test_clone_rejects_unknown_source(svc):
    with patch.object(svc, "list_instances", return_value=[_inst(0), _inst(1)]), \
            patch.object(svc, "_run_cmd") as run:
        with pytest.raises(ValueError):
            svc.clone_instance(7)
        run.assert_not_called()


# --------------------------------------------------- source đang tắt
def test_clone_stopped_source_issues_copy(svc):
    ld = FakeLD([0, 1])

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=99) as fin, \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        assert svc.clone_instance(0, name="clone-a") == 99

    copy = [c for c in ld.commands if c[0] == "copy"][0]
    # ldconsole only accepts --name BEFORE --from; the reverse order fails.
    assert copy == ["copy", "--name", "clone-a", "--from", "0"]
    fin.assert_called_once_with(2, True)


def test_clone_omits_name_when_not_given(svc):
    ld = FakeLD([0])

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=1), \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        svc.clone_instance(0)

    assert [c for c in ld.commands if c[0] == "copy"][0] == ["copy", "--from", "0"]


def test_clone_passes_phone_mode_flag(svc):
    ld = FakeLD([0])

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=5) as fin, \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        svc.clone_instance(0, phone_mode=False)
    fin.assert_called_once_with(1, False)


def test_clone_stopped_source_is_not_restarted(svc):
    ld = FakeLD([0])

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "quit_instance") as quit_, \
            patch.object(svc, "launch_instance") as launch, \
            patch.object(svc, "_finalize_new_instance", return_value=1), \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        svc.clone_instance(0)

    quit_.assert_not_called()
    launch.assert_not_called()


# --------------------------------------------------- source đang chạy
def test_clone_running_source_stops_then_restarts(svc):
    ld = FakeLD([0])

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=True), \
            patch.object(svc, "quit_instance", return_value=True) as quit_, \
            patch.object(svc, "_wait_stopped", return_value=True) as waited, \
            patch.object(svc, "launch_instance", return_value=True) as launch, \
            patch.object(svc, "_finalize_new_instance", return_value=77), \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        assert svc.clone_instance(0) == 77

    quit_.assert_called_once_with(0)
    waited.assert_called_once_with(0, timeout=180)
    launch.assert_called_once_with(0, wait_for_boot=False)


def test_clone_restarts_source_even_when_index_unknown(svc):
    """Nguồn vẫn phải được bật lại kể cả khi không xác định được index mới."""
    ld = FakeLD([0], hide_new=True)

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=True), \
            patch.object(svc, "quit_instance", return_value=True), \
            patch.object(svc, "_wait_stopped", return_value=True), \
            patch.object(svc, "launch_instance", return_value=True) as launch, \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        with pytest.raises(RuntimeError):
            svc.clone_instance(0)

    launch.assert_called_once_with(0, wait_for_boot=False)


def test_clone_name_argument_comes_before_from(svc):
    """Regression: ldconsole rejects "--from ... --name ..." with -1000.

    The reversed order makes it print the usage screen, which used to surface
    as "Da copy instance nhung khong xac dinh duoc index moi".
    """
    ld = FakeLD([0])
    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=1), \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        svc.clone_instance(0, "user@gmail.com")

    copy = [c for c in ld.commands if c[0] == "copy"][0]
    assert copy.index("--name") < copy.index("--from")
    assert copy == ["copy", "--name", "user", "--from", "0"]


def test_clone_uses_exit_code_as_new_index(svc):
    """ldconsole returns the new index as rc, used when list2 lags behind."""
    ld = FakeLD([0], hide_new=True, copy_rc=4)
    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=42) as fin, \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        assert svc.clone_instance(0) == 42
    fin.assert_called_once_with(4, True)


def test_clone_rejects_negative_exit_code(svc):
    """A negative rc means ldconsole refused the arguments."""
    ld = FakeLD([0], copy_stdout="usage screen", hide_new=True, copy_rc=-1000)
    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        with pytest.raises(RuntimeError):
            svc.clone_instance(0)


# ------------------------------------------------------------ index lookup
def test_clone_falls_back_to_parsed_index(svc):
    ld = FakeLD([0], copy_stdout="success index: 4", hide_new=True)

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_finalize_new_instance", return_value=42) as fin, \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        assert svc.clone_instance(0) == 42
    fin.assert_called_once_with(4, True)


def test_clone_raises_when_index_undeterminable(svc):
    ld = FakeLD([0], copy_stdout="", hide_new=True)

    with patch.object(svc, "list_instances", side_effect=ld.list_instances), \
            patch.object(svc, "is_running", return_value=False), \
            patch.object(svc, "_run_cmd", side_effect=ld.run_cmd):
        with pytest.raises(RuntimeError):
            svc.clone_instance(0)


# ------------------------------------------------------------------ api
def test_instance_create_model_carries_clone_from():
    import app as app_module

    assert app_module.InstanceCreate(name="x", phone_mode=True,
                                    clone_from=0).clone_from == 0
    assert app_module.InstanceCreate(name=None).clone_from is None