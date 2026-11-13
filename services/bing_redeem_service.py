import json
import re
import shlex
import time
from dataclasses import dataclass
from typing import Optional

from config import settings
from services.adb_service import adb_service
from services.ldplayer_service import ldplayer_service
from services import webview_cdp
from services.ui_text import node_attrs, same_text, text_matches, norm

PACKAGE = "com.microsoft.bing"

TEXT_VIEW_DETAIL = "View detail"
TEXT_REWARDS_DETAILS = "Rewards details"
TEXT_ORDER_DETAIL = "Order detail"
DESC_REDEEM_URL = "URL quy đổi"
DESC_PROFILE = "Hồ sơ"

GIFT_HOST = "gift.shopmygiftcards.com"
ACCEPT_LABELS = ("Accept", "Accept all", "Đồng ý", "Cho phép", "Tôi đồng ý")
MANAGE_LABELS = ("Manage cookies", "Quản lý cookie")



@dataclass
class StepResult:
    success: bool
    message: str


@dataclass
class RedeemInfo:
    email: str = ""
    points: Optional[int] = None
    order_date: str = ""
    product: str = ""
    order_no: str = ""
    order_points: Optional[int] = None
    voucher_url: str = ""
    reward_type: str = "N/A"
    # True when the balance already came from the live order history header,
    # i.e. Bing has deducted the order already, so no subtraction is needed.
    points_from_dom: bool = False


def classify_reward(name: str) -> str:
    """Map a Bing reward name to the voucher type used by the VIP dashboard.

    Order matters: 'grabmart' contains 'grab', so it has to be tested first.
    """
    n = (name or "").lower()
    if "jollibee" in n:
        return "Jollibee"
    if "grabmart" in n or "grab mart" in n:
        return "GrabMart"
    if "grabcar" in n or "grab car" in n:
        return "GrabCar"
    if "grab" in n:
        return "GrabCar"
    return "N/A"


def default_price(reward_type: str) -> int:
    return 30000 if reward_type == "GrabCar" else 70000


DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
NUM_RE = re.compile(r"^[\d.,\s]+$")


def parse_number(text: str) -> Optional[int]:
    """Read a points value written in either locale ('5,460' or '5.460')."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def today_str() -> str:
    import datetime
    now = datetime.datetime.now()
    return f"{now.month}/{now.day}/{now.year}"


def parse_date(text: str):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (year, month, day)


def extract_date(text: str):
    return DATE_RE.search(text or "").group(0) if DATE_RE.search(text or "") else None


SKIP_TEXTS = {
    "pts", "|", "điểm", "·", "-",
    "Order history", "Your orders", "Redeem", "Dashboard", "Earn", "About",
    "Refer and Earn", "View profile", "Microsoft Rewards logo", "back",
    "Lịch sử đơn hàng", "Rewards", "View detail", "Đã quy đổi",
    "Tìm kiếm", "Tìm hiểu thêm", "Cửa hàng", "Thắng", "Quyên góp",
    "Order history – Microsoft Rewards", "Microsoft Rewards",
    "Customer Support", "Bảng điều khiển", "Kiếm", "Giới thiệu",
    "Dashboard – Microsoft Rewards", "Thông tin đơn hàng",
}


def _is_chrome(text: str) -> bool:
        """True for app chrome that must never be mistaken for a product."""
        t = (text or "").strip()
        if not t or t.lower() in SKIP_TEXTS:
            return True
        if t.lower().startswith("http://") or t.lower().startswith("https://"):
            return True
        # Long descriptive sentences are page copy, not a reward name.
        return len(t) > 70 or " " in t and len(t.split()) > 6

def _is_order_anchor(text: str) -> bool:
    low = (text or "").lower()
    return low.startswith("order no.") or low.startswith("số đơn hàng")


def _order_no_from(text: str) -> str:
    low = text.lower()
    for prefix in ("order no.", "số đơn hàng"):
        if low.startswith(prefix):
            return text[len(prefix):].strip()
    return text.strip()


class BingRedeemAutomation:
    """Reads the Bing app: current points plus the newest order of the day.

    Everything happens on an already running instance - this service never
    launches or installs anything.
    """

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
        self._shell("uiautomator", "dump", "/sdcard/_redeem.xml", timeout=self.timeout + 30)
        return self._shell("cat", "/sdcard/_redeem.xml").stdout or ""

    def nodes(self, xml: str = None):
        """Visible nodes only - the WebView exposes off-screen items as [0,0][0,0]."""
        return [a for a in node_attrs(xml if xml is not None else self.dump())
                if a.get("bounds") != "[0,0][0,0]"]

    def find(self, xml: str, text: str = "", desc: str = "", cls: str = ""):
        for a in self.nodes(xml):
            if text and not same_text(a.get("text", ""), text):
                continue
            if desc and not same_text(a.get("content-desc", ""), desc):
                continue
            if cls and a.get("class") != cls:
                continue
            return a
        return None

    def find_contains(self, xml: str, needle: str, cls: str = ""):
        for a in self.nodes(xml):
            if not text_matches(a.get("text", ""), needle):
                continue
            if cls and a.get("class") != cls:
                continue
            return a
        return None

    def center(self, node) -> tuple:
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
        x1, y1, x2, y2 = map(int, m.groups())
        return (x1 + x2) // 2, (y1 + y2) // 2

    def tap(self, node) -> bool:
        if not node:
            return False
        x, y = self.center(node)
        self._shell("input", "tap", str(x), str(y))
        return True

    def wait_for(self, predicate, timeout: int = 45, interval: int = 4) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate(self.dump()):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    # ------------------------------------------------------------------ flow
    def app_in_foreground(self, xml: Optional[str] = None) -> bool:
        """True when the Bing WebView is the visible app (no restart needed)."""
        source = xml if xml is not None else self.dump()
        for a in self.nodes(source):
            if a.get("package") == PACKAGE and a.get("bounds") != "[0,0][0,0]":
                return True
        return False

    def is_order_history(self, xml: Optional[str] = None) -> bool:
        """True when the current page is the Rewards order history list."""
        source = xml if xml is not None else self.dump()
        return (self.find_contains(source, "Order no.") is not None
                or self.find(source, text="Order history") is not None)

    def read_profile_from_dom(self, attempts: int = 4,
                              delay: int = 4) -> RedeemInfo:
        """Read email + balance out of the live page DOM, without navigating.

        On the order history page the header already carries the balance and
        the signed-in address is embedded in the markup, so both can be read
        over CDP while the page stays exactly where the user left it.

        The balance sits between the nav links and the order list; it is
        matched by shape rather than by the currency label, because a
        Vietnamese locale renders "TĐ" where other locales render "VN".
        """
        js = r"""
        (() => {
          const out = {};
          try {
            const html = document.documentElement.outerHTML;
            const m = html.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
            out.email = m ? m[0] : "";
          } catch (e) { out.email = ""; }
          try {
            const lines = document.body.innerText.split("\n")
                .map(s => s.trim()).filter(s => s.length);
            const ordersAt = lines.findIndex(x => /your orders/i.test(x));
            out.balance = "";
            for (let i = 0; i < lines.length; i++) {
              const s = lines[i];
              if (!/^\d[\d.,]*$/.test(s)) continue;
              if (s.replace(/[.,]/g, "").length < 2) continue;
              if (ordersAt >= 0 && i >= ordersAt) break;   // past the order list
              out.balance = s;
              break;
            }
          } catch (e) { out.balance = ""; }
          return JSON.stringify(out);
        })()
        """
        data = {}
        # The devtools socket only appears once the WebView has finished
        # loading, so an early call legitimately comes back empty.
        for _ in range(max(1, attempts)):
            raw = webview_cdp.evaluate(js, serial=self.serial)
            data = {}
            if raw:
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    data = {}
            if data.get("email"):
                break
            time.sleep(delay)

        return RedeemInfo(email=(data.get("email") or "").strip(),
                          points=parse_number(data.get("balance") or ""))

    def open_app(self) -> bool:
        """Start the Bing app and wait until it has rendered something usable."""
        self._shell("am", "force-stop", PACKAGE)
        time.sleep(2)
        self._shell("monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")

        def ready(x):
            return (self.find(x, desc=DESC_PROFILE) is not None
                    or self.find_contains(x, "Tìm kiếm") is not None
                    or self.find_contains(x, "Rewards") is not None)

        if self.wait_for(ready, timeout=120, interval=4):
            return True
        return "com.microsoft.bing" in self.dump()

    def _on_profile_panel(self, xml: str) -> bool:
        texts = [a.get("text", "") for a in self.nodes(xml)]
        has_email = any("@" in t and "." in t.split("@")[-1] for t in texts)
        has_points = any(same_text(t, "Tổng số điểm") for t in texts)
        return has_email and has_points

    def read_profile(self) -> RedeemInfo:
        """Points + signed-in email from the Rewards panel.

        The app can restore itself onto a web page where the profile icon is
        not visible, so we walk back towards the home screen until the panel
        can be opened. Tapping the icon twice would drill into the account
        sub-menu, hence the guard below.
        """
        # Close any leftover dialog / web view the app restored into.
        for _ in range(4):
            xml = self.dump()
            if self.find(xml, desc=DESC_PROFILE) or self._on_profile_panel(xml):
                break
            if self.find(xml, text="Close") or self.find(xml, text=TEXT_ORDER_DETAIL):
                self._shell("input", "keyevent", "4")
                time.sleep(3)
                continue
            self._shell("input", "keyevent", "4")
            time.sleep(4)

        for attempt in range(4):
            xml = self.dump()
            if self._on_profile_panel(xml):
                break

            profile = self.find(xml, desc=DESC_PROFILE)
            if profile:
                self.tap(profile)
                if self.wait_for(self._on_profile_panel, timeout=25):
                    break
            else:
                self._shell("input", "keyevent", "4")
                time.sleep(4)

        xml = self.dump()
        if not self._on_profile_panel(xml):
            return RedeemInfo()

        email, points = "", None
        nodes = self.nodes(xml)
        has_label = any(same_text(b.get("text", ""), "Tổng số điểm") for b in nodes)
        for a in nodes:
            t = a.get("text", "").strip()
            if "@" in t and "." in t.split("@")[-1] and not email:
                email = t
            if has_label and points is None and NUM_RE.match(t):
                points = parse_number(t)
        return RedeemInfo(email=email, points=points)

    def dismiss_cookie_banner(self, scrolls: int = 4) -> bool:
        """Accept the cookie consent banner that blocks the order list.

        The banner sits below the fold, so the page is scrolled a little and
        the button is matched by its label instead of a fixed position.
        """
        for _ in range(scrolls):
            xml = self.dump()

            accept = None
            manage = None
            for a in self.nodes(xml):
                if a.get("class") != "android.widget.Button":
                    continue
                t = a.get("text", "").strip()
                if any(same_text(t, lbl) for lbl in ACCEPT_LABELS):
                    accept = a
                elif any(same_text(t, lbl) for lbl in MANAGE_LABELS):
                    manage = a
                elif manage is None and text_matches(t, "cookie", exact=False) \
                        and a.get("clickable") == "true":
                    manage = a

            if accept:
                self.tap(accept)
                time.sleep(4)
                return True
            if manage:
                # No visible Accept: open the cookie settings and pick it there.
                self.tap(manage)
                time.sleep(5)
                xml2 = self.dump()
                btn = None
                for a in self.nodes(xml2):
                    if a.get("class") == "android.widget.Button" and \
                            any(same_text(a.get("text", ""), lbl) for lbl in ACCEPT_LABELS):
                        btn = a
                        break
                if btn:
                    self.tap(btn)
                    time.sleep(4)
                    return True

            self._shell("input", "swipe", "360", 900, "360", 520, "400")
            time.sleep(3)

        return False

    def go_home(self) -> bool:
        """Return to the Bing home screen using the "<" back arrow.

        The Rewards panel is a full page, so leaving it restarts the app. The
        arrow keeps the session (and therefore avoids a second launch) while
        still bringing the search box into view.
        """
        for _ in range(4):
            xml = self.dump()
            if (self.find_contains(xml, "Tìm kiếm") is not None
                    or self.find(xml, text="Tìm kiếm") is not None):
                return True

            arrow = None
            for a in self.nodes(xml):
                if same_text(a.get("text", "").strip(), "back"):
                    arrow = a
                    break
                if not arrow and same_text(a.get("content-desc", ""), "Quay lại"):
                    arrow = a
            if arrow:
                self.tap(arrow)
            else:
                # No arrow visible: a back press leaves the rewards panel.
                self._shell("input", "keyevent", "4")
            time.sleep(4)

        xml = self.dump()
        return (self.find_contains(xml, "Tìm kiếm") is not None
                or self.find(xml, text="Tìm kiếm") is not None)

    def open_order_history(self) -> bool:
        """Open the order history by typing the URL into the Bing search box.

        Walking the Rewards menu is unreliable (the tabs are not exposed on
        the mobile layout), while the search box always accepts a URL. The app
        is NOT restarted here - read_profile() has already opened it once.
        """
        def on_history(x):
            return (self.find_contains(x, "Order no.") is not None
                    or self.find(x, text="Order history") is not None)

        if not self.go_home():
            # Only if the home screen is truly unreachable, start the app.
            self._shell("am", "force-stop", PACKAGE)
            time.sleep(2)
            self._shell("monkey", "-p", PACKAGE,
                        "-c", "android.intent.category.LAUNCHER", "1")
            self.wait_for(lambda x: self.find_contains(x, "Tìm kiếm") is not None,
                          timeout=90, interval=4)

        xml = self.dump()
        box = self.find_contains(xml, "Tìm kiếm") or self.find(xml, text="Tìm kiếm")
        if box and self.tap(box):
            time.sleep(3)
            self._shell("input", "text", shlex.quote(settings.apps.bing_order_history_url))
            time.sleep(3)
            self._shell("input", "keyevent", "66")     # ENTER
            if self.wait_for(on_history, timeout=120, interval=5):
                self.dismiss_cookie_banner()
                return True

        # Fallback: the menu route, in case the search box was unavailable.
        self.read_profile()
        xml = self.dump()
        rewards = self.find(xml, text="Microsoft Rewards")
        if rewards:
            self.tap(rewards)
            self.wait_for(lambda x: self.find_contains(x, "Lịch sử đơn hàng") is not None,
                          timeout=90, interval=5)
            for _ in range(3):
                xml = self.dump()
                link = self.find_contains(xml, "Lịch sử đơn hàng")
                if link:
                    self.tap(link)
                    if self.wait_for(on_history, timeout=60, interval=4):
                        return True
                self._shell("input", "swipe", "360", "950", "360", "400", "400")
                time.sleep(3)

        return on_history(self.dump())

    def _tap_view_detail(self, serial: str) -> bool:
        """Open the order detail.

        Clicking through CDP (scrollIntoView + click) instead of the
        accessibility tree: a partly visible button reports inverted bounds
        (e.g. [82,176][638,58], height -118), which previously made the UI
        code scroll forever without ever tapping.
        """
        for _ in range(4):
            result = webview_cdp.click_view_detail(serial)
            if result and "CLICKED" in result:
                return True
            # No button rendered yet - pull the page up so the list shows.
            self._shell("input", "swipe", "360", 950, "360", 600, "400")
            time.sleep(3)
        return False

    def open_today_order(self) -> RedeemInfo:
        """Open the detail of the newest order dated today.

        Order data is read from the page DOM over CDP; only the "View detail"
        button is driven through the accessibility tree, because clicking is
        what advances the page.
        """
        for _ in range(5):
            orders = webview_cdp.read_orders(self.serial)
            if orders:
                break
            self._shell("input", "swipe", "360", 900, "360", 560, "400")
            time.sleep(3)

        if not orders:
            return RedeemInfo(order_date="")

        today = today_str()
        todays = [o for o in orders if o.get("date") == today]
        candidates = sorted(todays or orders,
                            key=lambda o: parse_date(o.get("date", "")) or (0, 0, 0),
                            reverse=True)
        chosen = candidates[0]
        idx = orders.index(chosen)

        info = RedeemInfo(order_date=chosen.get("date", ""),
                          product=chosen.get("product") or "",
                          order_points=chosen.get("points"),
                          order_no=chosen.get("orderNo") or "")

        self._tap_view_detail(self.serial)

        time.sleep(6)
        if not self.wait_for(lambda x: self.find(x, text=TEXT_ORDER_DETAIL) is not None,
                             timeout=40):
            return info

        self.expand_rewards_details()
        return info

    def expand_rewards_details(self, attempts: int = 4) -> bool:
        """Open the "Rewards details" section until "URL quy đổi" is rendered.

        The section is an accordion that sometimes needs a second tap, and it
        shows a spinner before the URL row appears.
        """
        for _ in range(attempts):
            xml = self.dump()
            if self.find(xml, desc=DESC_REDEEM_URL) or self.find(xml, text=DESC_REDEEM_URL):
                return True
            toggle = self.find(xml, text=TEXT_REWARDS_DETAILS, cls="android.widget.Button")
            if not toggle:
                time.sleep(3)
                continue
            self.tap(toggle)
            time.sleep(5)
        xml = self.dump()
        return bool(self.find(xml, desc=DESC_REDEEM_URL)
                    or self.find(xml, text=DESC_REDEEM_URL))

    def grab_voucher_url(self) -> str:
        """Read the redemption URL straight from the page DOM over CDP.

        The link lives in an <a href>, which the Android accessibility tree
        does not expose, so the WebView's debug socket is used directly.
        """
        self.expand_rewards_details()
        return webview_cdp.voucher_url(self.serial) or ""


def collect_redeem_info(index: int, expected_email: str = "") -> StepResult:
    serial = ldplayer_service.get_serial(index)
    if not adb_service.ensure_device(serial, timeout=45):
        return StepResult(False, f"ADB {serial} khong online")

    bot = BingRedeemAutomation(serial)

    # Read the screen that is already open instead of restarting the app.
    xml = bot.dump()
    if not bot.app_in_foreground(xml):
        if not bot.open_app():
            return StepResult(False, "Khong mo duoc app Bing")
        xml = bot.dump()

    already_on_history = bot.is_order_history(xml)

    # On the order history page the profile is read from the DOM so the page
    # the user prepared is never navigated away from.
    points_from_dom = already_on_history
    profile = bot.read_profile_from_dom() if already_on_history else RedeemInfo()
    if already_on_history and not profile.email:
        # Without CDP neither the address nor the balance can be read here.
        # Navigating to the profile would tear down the order history page the
        # user prepared and press back several times, so fail with something
        # actionable instead of silently destroying their screen.
        return StepResult(False,
                          "Khong doc duoc email tren trang Order history. "
                          "WebView cua Bing chua bat debug - hay mo lai app Bing "
                          "roi thu lai.")
    if not already_on_history:
        profile = bot.read_profile()

    if not profile.email:
        return StepResult(False, "Khong doc duoc email tren man hinh")
    # Bing lower-cases the address on screen, so never compare case-sensitively.
    if expected_email and not same_text(profile.email, expected_email,
                                        case_sensitive=False):
        return StepResult(False,
                          f"Sai tai khoan: man hinh dang dang nhap {profile.email}, "
                          f"can {expected_email}")

    if not already_on_history:
        if not bot.open_order_history():
            return StepResult(False, "Khong mo duoc trang Order history")

    info = bot.open_today_order()
    info.email = profile.email
    info.points = profile.points
    info.points_from_dom = points_from_dom

    if info.order_date:
        url = bot.grab_voucher_url()
        info.voucher_url = url
        info.reward_type = classify_reward(info.product)

    return StepResult(True, info)