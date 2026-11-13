import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from config import settings
from services.adb_service import adb_service
from services.ldplayer_service import ldplayer_service
from services.ui_text import same_text, text_matches


PACKAGE = "io.oxylabs.proxymanager"
ACTIVITY = "io.oxylabs.proxymanager.MainActivity"

# The system VPN consent dialog is owned by this package, not by OxyProxy.
VPN_DIALOG_PKG = "com.android.vpndialogs"
VPN_ACCEPT_LABELS = ("OK", "Đồng ý", "Cho phép", "Accept", "Kết nối")


@dataclass
class StepResult:
    success: bool
    message: str


class _UiNode:
    __slots__ = ("text", "cls", "bounds", "clickable", "password", "focused", "rid", "pkg")

    def __init__(self, attrs: dict):
        self.text = attrs.get("text", "")
        self.cls = attrs.get("class", "")
        self.password = attrs.get("password") == "true"
        self.focused = attrs.get("focused") == "true"
        self.rid = attrs.get("resource-id", "")
        self.pkg = attrs.get("package", "")
        b = attrs.get("bounds", "")
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        self.bounds = tuple(int(g) for g in m.groups()) if m else None
        self.clickable = attrs.get("clickable") == "true"

    @property
    def center(self):
        if not self.bounds:
            return None
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


class OxyProxyAutomation:
    """Drives the OxyProxy "Add new proxy" form over ADB.

    The app is Jetpack Compose, so there are no resource-ids to target.
    Elements are located through their visible label text, and text entry
    relies on TAB navigation because the soft keyboard shifts the layout.
    """

    def __init__(self, serial: str, timeout: int = 60):
        self.serial = serial
        self.timeout = timeout

    # ---------------------------------------------------------------- helpers
    def _shell(self, *args, timeout: Optional[int] = None):
        # adb.exe rejects non-str arguments, so coerce everything.
        argv = [str(a) for a in args]
        return subprocess.run(
            [str(settings.ldplayer.adb_path), "-s", self.serial, "shell", *argv],
            capture_output=True, text=True,
            timeout=timeout or self.timeout,
            encoding="utf-8", errors="replace",
        )

    def dump(self) -> str:
        self._shell("uiautomator", "dump", "/sdcard/_oxy_ui.xml", timeout=self.timeout + 30)
        return self._shell("cat", "/sdcard/_oxy_ui.xml").stdout or ""

    @staticmethod
    def _parse(xml: str) -> List[_UiNode]:
        nodes = []
        for m in re.finditer(r"<node\s([^>]*?)/?>", xml):
            attrs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', m.group(1)))
            nodes.append(_UiNode(attrs))
        return nodes

    def find_by_text(self, xml: str, text: str, exact: bool = False) -> Optional[_UiNode]:
        for node in self._parse(xml):
            if text_matches(node.text, text, exact=exact) and node.bounds:
                return node
        return None

    def edit_texts(self, xml: str) -> List[_UiNode]:
        return [n for n in self._parse(xml) if n.cls == "android.widget.EditText"]

    def tap(self, node: _UiNode) -> bool:
        pos = node.center
        if not pos:
            return False
        self._shell("input", "tap", str(pos[0]), str(pos[1]))
        return True

    def type_text(self, value: str) -> None:
        """input text runs through a remote shell, so quoting is mandatory."""
        self._shell("input", "text", shlex.quote(value))

    def tab(self) -> None:
        self._shell("input", "keyevent", "61")

    def key(self, code: int) -> None:
        self._shell("input", "keyevent", str(code))

    def wait_for(self, predicate, timeout: int = 30, interval: int = 2) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate(self.dump()):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def wait_for_text(self, text: str, timeout: int = 25) -> bool:
        return self.wait_for(lambda x: self.find_by_text(x, text, exact=True) is not None, timeout)

    def field_below_label(self, label: str) -> Optional[_UiNode]:
        """Find the EditText sitting directly under a given label."""
        nodes = self._parse(self.dump())
        label_node = next((n for n in nodes
                           if same_text(n.text, label) and n.bounds), None)
        if not label_node:
            return None
        _, label_bottom = label_node.bounds[1], label_node.bounds[3]
        best = None
        for node in nodes:
            if node.cls != "android.widget.EditText" or not node.bounds:
                continue
            top = node.bounds[1]
            if top >= label_bottom and (best is None or top < best.bounds[1]):
                best = node
        return best

    # ------------------------------------------------------------------ flow
    def launch(self) -> None:
        self._shell("am", "force-stop", PACKAGE)
        time.sleep(2)
        self._shell("monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        # Wait until the list screen has rendered (Compose needs a moment).
        self.wait_for(
            lambda x: (self._on_form(x)
                       or self.find_by_text(x, "Add new proxy", exact=True) is not None
                       or self.find_by_text(x, "Add new", exact=True) is not None),
            timeout=40)

    # Markers that only exist inside the "Add New Proxy" form, so we never
    # confuse the form title with the "Add new proxy" button on the list page.
    FORM_MARKERS = ("Server IP", "Connection type")

    def _on_form(self, xml: str) -> bool:
        return any(self.find_by_text(xml, m, exact=True) for m in self.FORM_MARKERS)

    def open_add_form(self) -> StepResult:
        xml = self.dump()
        if self._on_form(xml):
            return StepResult(True, "Form da mo")

        # The button is labelled "Add new proxy" on an empty list and
        # "Add new" once at least one entry exists.
        node = (self.find_by_text(xml, "Add new proxy", exact=True)
                or self.find_by_text(xml, "Add new", exact=True))
        if not node:
            return StepResult(False, "Khong tim thay nut them proxy")
        self.tap(node)
        if self.wait_for(self._on_form, timeout=25):
            return StepResult(True, "Da mo form them proxy")
        return StepResult(False, "Khong mo duoc form them proxy")

    def fill_form(self, name: str, host: str, port: str,
                  username: str, password: str) -> StepResult:
        # Scroll to the top so the Name field is reachable.
        for _ in range(3):
            self._shell("input", "swipe", "360", "300", "360", 1000, "300")
        time.sleep(2)

        name_field = self.field_below_label("Name")
        if not name_field:
            return StepResult(False, "Khong tim thay truong Name")
        self.tap(name_field)
        time.sleep(2)
        self.type_text(name)

        # TAB walks Name -> Server IP -> Port -> Username -> Password.
        for value in (host, port, username, password):
            self.tab()
            time.sleep(1)
            self.type_text(value)

        self.key(4)  # close the keyboard so the Create button becomes visible
        time.sleep(2)
        return StepResult(True, "Da dien form")

    def submit(self) -> StepResult:
        for _ in range(4):
            xml = self.dump()
            node = self.find_by_text(xml, "Create", exact=True)
            if node:
                self.tap(node)
                time.sleep(4)
                xml = self.dump()
                if self.find_by_text(xml, "Create", exact=True):
                    return StepResult(False, "Nut Create van con tren man hinh")
                return StepResult(True, "Da tao proxy")
            self._shell("input", "swipe", "360", 900, "360", 400, "300")
            time.sleep(2)
        return StepResult(False, "Khong tim thay nut Create")

    # ------------------------------------------------------------ DF entries
    def _proxy_list_rows(self) -> List[_UiNode]:
        """Return the "DF" name nodes that belong to the Proxy List section."""
        nodes = self._parse(self.dump())
        header = next((n for n in nodes
                       if same_text(n.text, "Proxy List") and n.bounds), None)
        if not header:
            return []
        top = header.bounds[3]
        return [n for n in nodes
                if same_text(n.text, "DF") and n.bounds and n.bounds[1] >= top]

    def has_df(self) -> bool:
        return bool(self._proxy_list_rows())

    def handle_vpn_permission(self, timeout: int = 25) -> bool:
        """Accept the Android VPN confirmation dialog if it shows up.

        The dialog belongs to com.android.vpndialogs and its confirm button is
        android:id/button1. Matching on the owning package instead of the
        dialog title makes this work whatever the device locale renders.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            dialog = [n for n in self._parse(self.dump())
                      if n.pkg == VPN_DIALOG_PKG]
            if dialog:
                ok = next((n for n in dialog if n.rid == "android:id/button1"
                           and n.bounds), None)
                if ok is None:
                    ok = next((n for n in dialog if n.bounds and n.text
                               and any(same_text(n.text, lbl)
                                       for lbl in VPN_ACCEPT_LABELS)), None)
                if ok is not None:
                    self.tap(ok)
                    time.sleep(4)
                    return True
            time.sleep(2)
        return False

    def _is_connected(self, xml: str) -> bool:
        for node in self._parse(xml):
            if text_matches(node.text, "Connected to", exact=False):
                return True
        return False

    def activate_df(self) -> StepResult:
        """Tap the existing "DF" entry and make sure the proxy connects."""
        rows = self._proxy_list_rows()
        if not rows:
            return StepResult(False, "Khong tim thay proxy DF")

        self.tap(rows[0])
        time.sleep(3)

        # The first run raises the system VPN dialog.
        self.handle_vpn_permission()

        for _ in range(6):
            xml = self.dump()
            if self._is_connected(xml) and self.find_by_text(xml, "Disconnect", exact=True):
                return StepResult(True, "DF da ket noi")
            time.sleep(3)
        return StepResult(False, "DF khong ket noi duoc")

    # --------------------------------------------------------------- flow
    def configure(self, name: str, host: str, port: str,
                  username: str, password: str) -> StepResult:
        """Make sure a "DF" entry exists, then activate it."""
        self.launch()

        if self.has_df():
            return self.activate_df()

        opened = self.open_add_form()
        if not opened.success:
            return opened
        filled = self.fill_form(name, host, port, username, password)
        if not filled.success:
            return filled
        submitted = self.submit()
        if not submitted.success:
            return submitted
        return self.activate_df()


def configure_oxyproxy(index: int, name: str, host: str, port: str,
                       username: str, password: str) -> StepResult:
    serial = ldplayer_service.get_serial(index)
    if not adb_service.ensure_device(serial):
        return StepResult(False, f"ADB {serial} khong online")
    return OxyProxyAutomation(serial).configure(name, host, port, username, password)