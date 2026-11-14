import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.adb_service import ADBService


def _r(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def make_service(script):
    """ADBService whose _run is driven by `script(args) -> SimpleNamespace`."""
    svc = ADBService()
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return script(args)

    svc._run = fake_run
    return svc, calls


def _joined(calls):
    return [" ".join(c) for c in calls]


# ------------------------------------------------------------ resolve
def test_resolve_launcher_activity_parses_component():
    svc, calls = make_service(lambda args: _r(
        stdout="priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n"
               "com.microsoft.bing/com.microsoft.sapphire.app.main.LauncherAlias\n"))
    comp = svc.resolve_launcher_activity("emulator-5566", "com.microsoft.bing")
    assert comp == "com.microsoft.bing/com.microsoft.sapphire.app.main.LauncherAlias"
    assert any("resolve-activity" in c for c in _joined(calls))


def test_resolve_launcher_activity_none_when_no_component():
    svc, _ = make_service(lambda args: _r(stdout="priority=0 preferredOrder=0\n"))
    assert svc.resolve_launcher_activity("emulator-5566", "com.microsoft.bing") is None


# ------------------------------------------------------------- launch
def test_launch_package_uses_am_start_when_activity_resolved():
    def script(args):
        s = " ".join(args)
        if "resolve-activity" in s:
            return _r(stdout="header\ncom.microsoft.bing/com.microsoft.sapphire.app.main.LauncherAlias\n")
        if "am start" in s:
            return _r(stdout="Starting: Intent { cmp=com.microsoft.bing/com.microsoft.sapphire.app.main.LauncherAlias }")
        return _r(returncode=1)

    svc, calls = make_service(script)
    assert svc.launch_package("emulator-5566", "com.microsoft.bing") is True
    assert not any("monkey" in c for c in _joined(calls)), \
        "am start succeeded; monkey must not run"


def test_launch_package_falls_back_to_monkey_when_am_start_errors():
    # `am start` exits 0 even on "Error type 3" - only the output tells.
    def script(args):
        s = " ".join(args)
        if "resolve-activity" in s:
            return _r(stdout="com.microsoft.bing/SomeActivity\n")
        if "am start" in s:
            return _r(stdout="Starting: Intent { cmp=com.microsoft.bing/SomeActivity }\n"
                             "Error type 3\nError: Activity class does not exist.\n")
        if "monkey" in s:
            return _r(stdout="Events injected: 1")
        return _r(returncode=1)

    svc, calls = make_service(script)
    assert svc.launch_package("emulator-5566", "com.microsoft.bing") is True
    assert any("monkey" in c for c in _joined(calls))


def test_launch_package_monkey_fallback_when_resolve_unavailable():
    # An image without `cmd` (or with it broken): monkey must still run.
    def script(args):
        s = " ".join(args)
        if "resolve-activity" in s:
            return _r(returncode=127, stderr="/system/bin/sh: cmd: inaccessible or not found")
        if "monkey" in s:
            return _r(stdout="Events injected: 1")
        return _r(returncode=1)

    svc, calls = make_service(script)
    assert svc.launch_package("emulator-5566", "com.microsoft.bing") is True
    assert any("monkey" in c for c in _joined(calls))


def test_launch_package_false_when_nothing_works():
    # LD14 reproduces exactly this: neither cmd resolve-activity nor monkey.
    def script(args):
        s = " ".join(args)
        if "resolve-activity" in s:
            return _r(returncode=127, stderr="/system/bin/sh: cmd: inaccessible or not found")
        if "monkey" in s:
            return _r(returncode=127, stderr="/system/bin/sh: monkey: inaccessible or not found")
        return _r(returncode=1)

    svc, calls = make_service(script)
    assert svc.launch_package("emulator-5566", "com.microsoft.bing") is False


def test_launch_package_am_start_treated_as_failure_on_error_output():
    def script(args):
        s = " ".join(args)
        if "resolve-activity" in s:
            return _r(stdout="com.microsoft.bing/SomeActivity\n")
        if "am start" in s:
            return _r(returncode=0, stdout="Error: Activity class does not exist.\n")
        return _r(returncode=1)

    svc, _ = make_service(script)
    assert svc.launch_package("emulator-5566", "com.microsoft.bing") is False
