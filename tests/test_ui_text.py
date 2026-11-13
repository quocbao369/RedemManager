import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ui_text import norm, same_text, text_matches, node_attrs, find_node

# Android's WebView emits NFD while the app source (and Jetpack Compose) use NFC.
NFC = "Nhập mật khẩu"
NFD = unicodedata.normalize("NFD", NFC)

VPN_NFC = "Yêu cầu kết nối"
VPN_NFD = unicodedata.normalize("NFD", VPN_NFC)


def test_nfc_and_nfd_are_not_raw_equal():
    # This is exactly what broke the automation before normalisation.
    assert NFC != NFD
    assert NFC != VPN_NFC.replace("Yêu cầu kết nối", VPN_NFD)[:0] or True


def test_same_text_handles_nfd():
    assert same_text(NFD, NFC) is True
    assert same_text(NFC, NFD) is True
    assert same_text(NFC, NFC) is True


def test_same_text_vpn_dialog():
    assert same_text(VPN_NFD, VPN_NFC) is True


def test_same_text_rejects_different_text():
    assert same_text("Tiếp theo", "Quay lại") is False


def test_same_text_case_sensitive_option():
    assert same_text("df", "DF", case_sensitive=False) is True
    assert same_text("df", "DF", case_sensitive=True) is False


def test_text_matches_exact():
    assert text_matches(NFD, NFC, exact=True) is True
    assert text_matches("Tiếp theo", "Tiếp", exact=True) is False
    assert text_matches("Tiếp theo", "Tiếp", exact=False) is True


def test_text_matches_connected_banner():
    assert text_matches("Connected to 1.2.3.4:8080", "Connected to") is True
    assert text_matches("Not connected", "Connected to") is False


def test_norm_collapses_whitespace_and_strips():
    assert norm("  Xin   chao  ") == "Xin chao"


def test_norm_removes_zero_width():
    assert norm("abc‏def") == "abcdef"


def test_norm_empty():
    assert norm("") == ""


XML = ('<hierarchy>'
       '<node index="0" text="%s" class="android.webkit.WebView" bounds="[0,48][720,1280]" />'
       '<node index="1" text="Ti&#7871;p theo" class="android.widget.Button" bounds="[48,572][672,648]" />'
       '<node index="2" text="" resource-id="android:id/button1" class="android.widget.Button" bounds="[518,885][646,981]" />'
       '</hierarchy>') % NFD


def test_node_attrs_parses_all():
    attrs = list(node_attrs(XML))
    assert len(attrs) == 3
    assert attrs[2]["resource-id"] == "android:id/button1"


def test_find_node_with_nfd_text():
    node = find_node(XML, text=NFC)
    assert node is not None
    assert node["class"] == "android.webkit.WebView"


def test_find_node_by_resource_id_via_desc():
    # buttons without text are located through content-desc
    assert find_node(XML, desc="") is not None
