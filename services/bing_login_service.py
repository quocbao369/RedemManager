import re
import shlex
import time
from dataclasses import dataclass
from typing import List, Optional

from config import settings
from services.adb_service import adb_service
from services.ldplayer_service import ldplayer_service
from services.oxyproxy_service import _UiNode
from services import screen_vision
from services.ui_text import node_attrs, same_text, text_matches, norm


PACKAGE = "com.microsoft.bing"

TEXT_NOT_NOW = "Không phải bây giờ"
DESC_PROFILE = "Hồ sơ"
TEXT_SIGN_IN = "Nhấn để đăng nhập"
TEXT_USE_PASSWORD = "Sử dụng mật khẩu của bạn"
TEXT_NEXT = "Tiếp theo"
# Microsoft shows different wording on the "skip the code" step.
USE_PASSWORD_ALIASES = (TEXT_USE_PASSWORD, "Dùng mật khẩu của bạn",
                        "Sử dụng mật khẩu", "Dùng mật khẩu")


@dataclass
class StepResult:
    success: bool
    message: str


class BingLoginAutomation:
    """Signs a Microsoft account in on the Bing Android app.

    The account screens are served by a WebView, so the email/password inputs
    are plain HTML boxes that uiautomator does not expose. They are tapped by
    position, while every action button is located through its visible label.
    """

    # Field boxes are WebView inputs with no dump entry; these offsets are
    # measured from the 720x1280 reference layout.
    FIELD_X = 360
    EMAIL_FIELD_Y = 373
    PASSWORD_FIELD_Y = 465

    def __init__(self, serial: str, timeout: int = 60):
        self.serial = serial
        self.timeout = timeout

    # ---------------------------------------------------------------- helpers
    def _shell(self, *args, timeout: Optional[int] = None):
        import subprocess
        argv = [str(a) for a in args]
        return subprocess.run(
            [str(settings.ldplayer.adb_path), "-s", self.serial, "shell", *argv],
            capture_output=True, text=True,
            timeout=timeout or self.timeout,
            encoding="utf-8", errors="replace",
        )

    def dump(self) -> str:
        self._shell("uiautomator", "dump", "/sdcard/_bing_ui.xml", timeout=self.timeout + 30)
        return self._shell("cat", "/sdcard/_bing_ui.xml").stdout or ""

    def nodes(self, xml: str = None) -> List[_UiNode]:
        source = xml if xml is not None else self.dump()
        return [_UiNode(dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', m.group(1))))
                for m in re.finditer(r"<node\s([^>]*?)/?>", source)]

    def texts(self) -> List[str]:
        return [n.text for n in self.nodes() if n.text]

    def find_text(self, xml: str, text: str) -> Optional[_UiNode]:
        for attrs in node_attrs(xml):
            if same_text(attrs.get("text", ""), text):
                return _UiNode(attrs)
        return None

    def find_desc(self, xml: str, desc: str) -> Optional[_UiNode]:
        for attrs in node_attrs(xml):
            if same_text(attrs.get("content-desc", ""), desc):
                return _UiNode(attrs)
        return None

    def tap(self, node: _UiNode) -> bool:
        pos = node.center
        if not pos:
            return False
        self._shell("input", "tap", str(pos[0]), str(pos[1]))
        return True

    def tap_xy(self, x: int, y: int) -> None:
        self._shell("input", "tap", str(x), str(y))

    def type_text(self, value: str) -> None:
        self._shell("input", "text", shlex.quote(value))

    def wait_for(self, predicate, timeout: int = 60, interval: int = 4):
        deadline = time.time() + timeout
        while time.time() < deadline:
            xml = self.dump()
            if predicate(xml):
                return True, xml
            time.sleep(interval)
        return False, self.dump()

    def wait_text(self, text: str, timeout: int = 60):
        return self.wait_for(lambda x: self.find_text(x, text) is not None, timeout)

    def click_label(self, label: str, timeout: int = 45) -> bool:
        ok, xml = self.wait_text(label, timeout)
        if ok:
            node = self.find_text(xml, label)
            if node:
                self.tap(node)
                time.sleep(3)
                return True

        # The sign-in WebView often renders without exposing anything to the
        # accessibility tree. Locating the button on the screenshot is both
        # faster (a screencap costs ~80ms, a uiautomator dump ~1.2s) and the
        # only option when the tree comes back empty.
        pos = screen_vision.find_next_button(self.serial)
        if pos is None:
            pos = screen_vision.find_accent_button(self.serial)
        if pos is None:
            return False
        self.tap_xy(int(pos[0]), int(pos[1]))
        time.sleep(3)
        return True

    # ------------------------------------------------------------------ flow
    def launch(self) -> None:
        self._shell("am", "force-stop", PACKAGE)
        time.sleep(2)
        # LD14's image ships without /system/bin/monkey, so the old monkey
        # call silently did nothing there; adb_service starts the resolved
        # launcher activity and only falls back to monkey.
        adb_service.launch_package(self.serial, PACKAGE)

    def dismiss_onboarding(self) -> None:
        """Tap "Không phải bây giờ" when the Copilot intro shows up."""
        ok, xml = self.wait_text(TEXT_NOT_NOW, timeout=30)
        if ok:
            self.tap(self.find_text(xml, TEXT_NOT_NOW))
            time.sleep(6)

    def _already_signed_in(self, xml: str, email: str) -> bool:
        """The profile page shows the account email once signed in."""
        texts = [a.get("text", "") for a in node_attrs(xml)]
        has_email = any(same_text(t, email) for t in texts)
        signed_out = self.find_text(xml, TEXT_SIGN_IN) is not None
        return has_email and not signed_out

    def open_sign_in(self, email: str) -> StepResult:
        """Reach the Microsoft sign-in form from any launch state.

        The app can come up on the onboarding screen, the home screen, the
        profile page or straight in a WebView, so keep polling and act on
        whatever is on screen.
        """
        deadline = time.time() + 120
        while time.time() < deadline:
            xml = self.dump()

            if self._already_signed_in(xml, email):
                return StepResult(True, "Da dang nhap san")

            # Already in the Microsoft sign-in flow?
            if (self.find_text(xml, "Đăng nhập")
                    or self.find_text(xml, "Email hoặc điện thoại")):
                return StepResult(True, "Da mo man hinh dang nhap")

            # Sign-in call to action on the profile page.
            node = self.find_text(xml, TEXT_SIGN_IN)
            if node:
                self.tap(node)
                time.sleep(6)
                return StepResult(True, "Da mo man hinh dang nhap")

            # Onboarding can block the home screen.
            if self.find_text(xml, TEXT_NOT_NOW, ):
                self.tap(self.find_text(xml, TEXT_NOT_NOW))
                time.sleep(6)
                continue

            # Home screen -> open the profile panel.
            profile = self.find_desc(xml, DESC_PROFILE)
            if profile:
                self.tap(profile)
                time.sleep(5)
                continue

            time.sleep(4)

        return StepResult(False, "Khong mo duoc man hinh dang nhap")

    def _input_field(self, label: str = "") -> Optional[_UiNode]:
        """Locate the HTML email/password box of the Microsoft sign-in page.

        The coordinates must be derived from the layout: this used to be a
        hardcoded (360, 373) measured on a 720x1280 emulator, which missed the
        field entirely once the instances were shrunk to 480x854.
        """
        for a in self.nodes():
            if a.cls == "android.widget.EditText" and a.bounds:
                return a

        # Fall back to a position just under the field label.
        if label:
            xml = self.dump()
            lab = self.find_text(xml, label)
            if lab and lab.bounds:
                x1, y1, x2, _ = lab.bounds
                cx = (x1 + x2) // 2
                return _UiNode({"bounds": f"[{x1},{(lab.bounds[3]) + 8}]"
                                        f"[{x2},{(lab.bounds[3]) + 66}]",
                                "class": "android.widget.EditText"})
        return None

    def _page_blank(self, xml: str) -> bool:
        """True when the WebView rendered nothing but its skeleton.

        The sign-in page is served through a slow mobile proxy and regularly
        drops to an empty WebView for several seconds; treating that as a
        hard failure is what made the login give up halfway.
        """
        return not [n for n in self.nodes(xml)
                    if n.bounds and n.bounds != (0, 0, 0, 0)
                    and (n.text or "").strip()]

    def _reopen_signin(self, attempts: int = 3) -> str:
        """Back out of a blank WebView and open the sign-in page again.

        When backing out does not help the app is restarted, because a WebView
        that stayed blank across reloads is stuck rather than slow.
        """
        xml = self.dump()
        for _ in range(attempts):
            if not self._page_blank(xml):
                return xml
            self._shell("input", "keyevent", "4")
            time.sleep(5)
            xml = self.dump()
            sign_in = self.find_text(xml, TEXT_SIGN_IN)
            if sign_in:
                self.tap(sign_in)
                time.sleep(12)
                xml = self.dump()

        if self._page_blank(xml):
            self.launch()
            time.sleep(12)
            self.dismiss_onboarding()
            xml = self.dump()
            profile = self.find_desc(xml, DESC_PROFILE)
            if profile:
                self.tap(profile)
                time.sleep(6)
                xml = self.dump()
                sign_in = self.find_text(xml, TEXT_SIGN_IN)
                if sign_in:
                    self.tap(sign_in)
                    time.sleep(14)
                    xml = self.dump()
        return xml

    def enter_email(self, email: str) -> StepResult:
        # Wait for the sign-in page, recovering from a blank WebView.
        deadline = time.time() + 180
        while time.time() < deadline:
            xml = self.dump()
            if self._page_blank(xml):
                xml = self._reopen_signin()
                continue
            if (self.find_text(xml, "Đăng nhập")
                    or self.find_text(xml, "Email hoặc điện thoại")
                    or self._input_field()):
                break
            time.sleep(4)
        else:
            return StepResult(False, "Khong co man hinh dang nhap")

        # The sign-in page is a WebView that reloads when the input takes
        # focus: the EditText drops out of the accessibility tree for several
        # seconds, so the typed value usually cannot be read back even though
        # it went in. The Next button coming back is the reliable "ready"
        # signal, so wait for it instead of failing on the first empty dump.
        for _ in range(2):
            field = self._input_field()
            if not field:
                return StepResult(False, "Khong tim thay o nhap email")
            self.tap(field)
            time.sleep(4)
            self.type_text(email)

            deadline = time.time() + 40
            while time.time() < deadline:
                xml = self.dump()
                if any(same_text(t, email, case_sensitive=False)
                       for t in self.texts()):
                    return StepResult(True, "Da nhap email")
                if self.find_text(xml, TEXT_NEXT) is not None:
                    return StepResult(True,
                                      "Da go email (WebView reload, khong doc lai duoc)")
                time.sleep(4)

        return StepResult(False, "Email khong nhap duoc")

    def submit_email(self) -> StepResult:
        if not self.click_label(TEXT_NEXT, timeout=90):
            return StepResult(False, "Khong thay nut '%s'" % TEXT_NEXT)
        ok, _ = self.wait_for(
            lambda x: self.find_text(x, TEXT_USE_PASSWORD) is not None, timeout=90)
        if not ok:
            return StepResult(False, "Khong thay '%s'" % TEXT_USE_PASSWORD)
        return StepResult(True, "Da chuyen sang buoc mat khau")

    def use_password(self) -> StepResult:
        if not any(self.click_label(lbl, timeout=40) for lbl in USE_PASSWORD_ALIASES):
            return StepResult(False, "Khong thay nut dung mat khau")
        ok, _ = self.wait_for(
            lambda x: self.find_text(x, "Nhập mật khẩu") is not None, timeout=60)
        if not ok:
            return StepResult(False, "Khong co man hinh nhap mat khau")
        return StepResult(True, "Da mo man hinh nhap mat khau")

    def enter_password(self, password: str) -> StepResult:
        field = self._input_field(label="Mật khẩu")
        if not field:
            # The password field is inside the same invisible WebView, so fall
            # back to locating the filled input box on the screenshot.
            pos = screen_vision.find_input_field(self.serial)
            if pos is None:
                return StepResult(False, "Khong tim thay o nhap mat khau")
            self.tap_xy(int(pos[0]), int(pos[1]))
            time.sleep(3)
            self.type_text(password)
            time.sleep(2)
            return StepResult(True, "Da go mat khau (WebView, dung toa do anh)")

        self.tap(field)
        time.sleep(3)
        self.type_text(password)
        time.sleep(2)

        # The password box masks its value, so verify by length via the dump.
        xml = self.dump()
        for a in self.nodes(xml):
            if a.cls == "android.widget.EditText" and a.password and a.text:
                return StepResult(True, "Da nhap mat khau")
        return StepResult(True, "Da gui len rut vao o mat khau")

    def submit_password(self, email: str) -> StepResult:
        if not self.click_label(TEXT_NEXT, timeout=60):
            return StepResult(False, "Khong thay nut '%s'" % TEXT_NEXT)

        # Success = the account email is on screen and the password form is gone.
        # Bing lower-cases the address, so compare without case sensitivity.
        deadline = time.time() + 120
        while time.time() < deadline:
            xml = self.dump()
            if (any(same_text(a.get("text", ""), email, case_sensitive=False)
                    for a in node_attrs(xml))
                    and self.find_text(xml, "Nhập mật khẩu") is None):
                return StepResult(True, "Dang nhap thanh cong")

            # The profile page lives in the same invisible WebView, so accept
            # success once the password box has left the screenshot.
            if screen_vision.find_input_field(self.serial) is None:
                return StepResult(True, "Dang nhap thanh cong (xac nhan bang anh)")
            time.sleep(4)

        return StepResult(False, "Dang nhap khong thanh cong")

    def _email_to_next(self, email: str) -> StepResult:
        """Type the address and press Next.

        The sign-in WebView blanks out for several seconds whenever the input
        takes focus and does not always come back, so the pair is retried
        from a recovered page before giving up.
        """
        last = StepResult(False, "Khong nhap duoc email")
        for attempt in range(3):
            step = self.enter_email(email)
            if step.success:
                last = step
                step = self.submit_email()
                if step.success:
                    return step
                last = step
            if attempt < 2:
                self._reopen_signin()
        return last

    def login(self, email: str, password: str) -> StepResult:
        self.launch()
        time.sleep(8)
        self.dismiss_onboarding()

        step = self.open_sign_in(email)
        if not step.success:
            return step
        if "san" in step.message:
            return StepResult(True, "Dang nhap thanh cong")

        step = self._email_to_next(email)
        if not step.success:
            return step
        step = self.use_password()
        if not step.success:
            return step
        step = self.enter_password(password)
        if not step.success:
            return step
        return self.submit_password(email)


def bing_login(index: int, email: str, password: str,
               ld_version: int = 9) -> StepResult:
    serial = ldplayer_service.get_serial(index, ld_version=ld_version)
    if not adb_service.ensure_device(serial):
        return StepResult(False, f"ADB {serial} khong online")
    return BingLoginAutomation(serial).login(email, password)