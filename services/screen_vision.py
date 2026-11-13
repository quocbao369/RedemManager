"""Locate on-screen buttons from a screenshot.

The Bing sign-in screen is a WebView that renders normally but often leaves
nothing in the accessibility tree, so uiautomator cannot resolve its buttons.
The primary call-to-action is a solid accent-coloured rectangle, which is
cheap to isolate by colour and stays identical across screen sizes.
"""

import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from config import settings

# The accent colour of the Microsoft "Next" button, in HSV.
ACCENT_LOW = (90, 40, 60)
ACCENT_HIGH = (140, 255, 255)

# Fallback geometry, measured from a rendered sign-in page at 480x854.
DEFAULT_BUTTON_CENTER = (333, 456)
DEFAULT_BUTTON_SIZE = (162, 42)


def _adb_path() -> Path:
    return Path(settings.ldplayer.adb_path)


_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "data" / "img" / "btnNext.png"
# The reference is a crop of the button rather than the whole control, so a
# small scale sweep recovers the match when the density differs.
TEMPLATE_SCALES = (1.0, 1.1, 0.9, 1.2)
_template_cache: Dict[Tuple[str, float], Optional[np.ndarray]] = {}


def _load_template(scale: float = 1.0) -> Optional[np.ndarray]:
    """Grey version of the reference Next button, scaled, loaded once."""
    key = (str(_TEMPLATE_PATH), scale)
    if key not in _template_cache:
        img = cv2.imread(_TEMPLATE_PATH, cv2.IMREAD_GRAYSCALE)
        if img is not None and scale != 1.0:
            img = cv2.resize(img, (max(8, int(img.shape[1] * scale)),
                                   max(8, int(img.shape[0] * scale))))
        _template_cache[key] = img
    return _template_cache[key]


def find_next_button(serial: str, threshold: float = 0.55,
                     img: Optional[np.ndarray] = None) -> Optional[Tuple[int, int]]:
    """Locate the Next button by matching the reference image.

    Faster than the colour scan and independent of the accessibility tree,
    which the sign-in WebView often leaves empty.
    """
    frame = img if img is not None else screenshot(serial)
    if frame is None:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    best = None
    for scale in TEMPLATE_SCALES:
        tpl = _load_template(scale)
        if tpl is None:
            continue
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            continue
        result = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _ml, mxl = cv2.minMaxLoc(result)
        if best is None or mx > best[0]:
            th, tw = tpl.shape[:2]
            best = (mx, (mxl[0] + tw // 2, mxl[1] + th // 2))

    if best is None or best[0] < threshold:
        return None
    return best[1]


def screenshot(serial: str) -> Optional[np.ndarray]:
    try:
        result = subprocess.run(
            [_adb_path(), "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    buf = np.frombuffer(result.stdout, np.uint8)
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def is_blank(img: np.ndarray) -> bool:
    """True when the screen carries almost no visual information."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(gray.std()) < 12


def find_accent_button(serial: str,
                      min_w: int = 80, max_w: int = 450,
                      min_h: int = 12, max_h: int = 90,
                      min_area: int = 600) -> Optional[Tuple[int, int]]:
    """Return the centre of the primary action button, or None.

    The button is the accent-coloured shape lowest on the screen. A full-width
    solid block is the focused input box rather than the button, and is
    skipped; a text-only button is thin, so the fill ratio is not used to
    decide what counts as a button.
    """
    img = screenshot(serial)
    if img is None or is_blank(img):
        return None

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(ACCENT_LOW), np.array(ACCENT_HIGH))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    count, _labels, stats, _cents = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if not (min_w <= w <= max_w and min_h <= h <= max_h):
            continue
        if area < min_area:
            continue
        fill = area / float(w * h)
        if fill >= 0.95 and w >= 380:
            continue                      # focused input box, not the button
        if best is None or y > best[0]:   # lowest accent shape wins
            best = (y, (x + w // 2, y + h // 2))
    return best[1] if best else None


def find_input_field(serial: str,
                     min_w_ratio: float = 0.55,
                     fill_ratio: float = 0.75) -> Optional[Tuple[int, int]]:
    """Return the centre of the active text field, or None.

    An input box paints its whole width on nearly every row, while a line of
    text only ever covers a fraction of the row. Grouping the rows that are
    densely filled therefore isolates the field from the headings around it.
    """
    img = screenshot(serial)
    if img is None or is_blank(img):
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    rows = (gray < 235).sum(axis=1) >= int(w * fill_ratio)

    bands, start = [], None
    for y, filled in enumerate(rows):
        if y < 60 and filled:
            # Status bar is a solid strip and must not be mistaken for a field.
            if start is not None:
                bands.append((start, y - 1))
                start = None
            continue
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, h - 1))

    for top, bottom in bands:
        if not (30 <= bottom - top <= 130):   # a field, not a bar or a rule
            continue
        return w // 2, (top + bottom) // 2
    return None


def default_button_center(serial: str) -> Tuple[int, int]:
    """Last-resort coordinate, scaled from the 480x854 reference layout."""
    img = screenshot(serial)
    if img is None:
        return DEFAULT_BUTTON_CENTER
    h, w = img.shape[:2]
    ref_w, ref_h = 480, 854
    x, y = DEFAULT_BUTTON_CENTER
    return int(round(x * w / ref_w)), int(round(y * h / ref_h))