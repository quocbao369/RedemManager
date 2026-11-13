import re
import unicodedata

__all__ = [
    "norm", "same_text", "text_matches", "iter_nodes", "node_attrs", "find_node",
]


def norm(value: str) -> str:
    """Normalise text for comparison.

    Android renderers are inconsistent: Jetpack Compose emits NFC while the
    WebView (Microsoft sign-in pages) emits NFD, where "ậ" is "â" + a
    combining circumflex accent. Both render identically but compare unequal,
    so every lookup has to go through this helper.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    # Drop zero-width and other invisible characters the WebView injects.
    text = re.sub(r"[-‏⁠﻿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def same_text(a: str, b: str, case_sensitive: bool = True) -> bool:
    na, nb = norm(a), norm(b)
    if not case_sensitive:
        na, nb = na.casefold(), nb.casefold()
    return na == nb


def text_matches(haystack: str, needle: str, exact: bool = False,
                 case_sensitive: bool = False) -> bool:
    nh = norm(haystack)
    nn = norm(needle)
    if not case_sensitive:
        nh, nn = nh.casefold(), nn.casefold()
    return nh == nn if exact else nn in nh


def node_attrs(xml: str):
    for m in re.finditer(r"<node\s([^>]*?)/?>", xml):
        yield dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', m.group(1)))


def iter_nodes(xml: str):
    return node_attrs(xml)


def find_node(xml: str, text: str = "", desc: str = "", exact: bool = True):
    """Return the first node matching a text label and/or a content-desc."""
    for attrs in node_attrs(xml):
        if text and not text_matches(attrs.get("text", ""), text, exact=exact):
            continue
        if desc and not same_text(attrs.get("content-desc", ""), desc):
            continue
        return attrs
    return None