import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.oxyproxy_service import OxyProxyAutomation


def _node(text="", cls="android.widget.TextView", bounds="[0,0][1,1]",
          rid="", clickable="false", focused="false", password="false"):
    return (f'<node index="0" text="{text}" resource-id="{rid}" class="{cls}" '
            f'clickable="{clickable}" enabled="true" focusable="true" focused="{focused}" '
            f'password="{password}" bounds="{bounds}" />')


@pytest.fixture
def automation():
    return OxyProxyAutomation("dummy-serial")


EMPTY_SCREEN = "<hierarchy>" + _node("Create Your First Proxy Entry", bounds="[64,300][656,360]") \
    + _node("Add new proxy", bounds="[252,800][469,839]") + "</hierarchy>"

LIST_SCREEN = (
    "<hierarchy>"
    + _node("Proxy List", bounds="[64,648][364,739]")
    + _node("DF", bounds="[96,838][130,877]")
    + _node("1.2.3.4:8080", bounds="[96,885][293,919]")
    + _node("Disconnect", bounds="[468,861][608,895]")
    + _node("DF", bounds="[96,1012][130,1051]")
    + _node("5.6.7.8:9090", bounds="[96,1059][321,1093]")
    + "</hierarchy>"
)

VPN_DIALOG = (
    '<hierarchy package="com.android.vpndialogs">'
    + _node("Yêu cầu kết nối", bounds="[98,375][622,429]")
    + _node("HỦY", cls="android.widget.Button", rid="android:id/button2", bounds="[390,885][518,981]")
    + _node("OK", cls="android.widget.Button", rid="android:id/button1", bounds="[518,885][646,981]")
    + "</hierarchy>"
)


def test_has_df_false_on_empty_list(automation):
    automation.dump = lambda: EMPTY_SCREEN
    assert automation.has_df() is False


def test_has_df_true_when_present(automation):
    automation.dump = lambda: LIST_SCREEN
    assert automation.has_df() is True


def test_proxy_list_rows_returns_only_entries(automation):
    automation.dump = lambda: LIST_SCREEN
    rows = automation._proxy_list_rows()
    # The big "DF" title above the list header must not be counted.
    assert len(rows) == 2
    assert all(r.bounds[1] >= 739 for r in rows)


def test_proxy_list_rows_empty_without_header(automation):
    automation.dump = lambda: EMPTY_SCREEN
    assert automation._proxy_list_rows() == []


def test_find_by_text_exact(automation):
    automation.dump = lambda: LIST_SCREEN
    node = automation.find_by_text(LIST_SCREEN, "DF", exact=True)
    assert node is not None and node.bounds == (96, 838, 130, 877)
    assert automation.find_by_text(LIST_SCREEN, "Zzz", exact=True) is None


def test_is_connected_detects_banner(automation):
    connected = "<hierarchy>" + _node("Connected to 1.2.3.4:8080") + "</hierarchy>"
    assert automation._is_connected(connected) is True
    assert automation._is_connected(LIST_SCREEN) is False


def test_vpn_dialog_button_detection(automation):
    """The confirmation button must be found by its stable resource-id."""
    nodes = automation._parse(VPN_DIALOG)
    ids = [n.rid for n in nodes]
    assert "android:id/button1" in ids
    ok = next(n for n in nodes if n.rid == "android:id/button1")
    assert ok.center == (582, 933)
    assert any(n.text == "Yêu cầu kết nối" for n in nodes)


def test_field_below_label(automation):
    xml = (
        "<hierarchy>"
        + _node("Name", bounds="[64,400][200,440]")
        + _node("", cls="android.widget.EditText", bounds="[88,447][632,543]")
        + _node("Server IP", bounds="[64,600][200,640]")
        + _node("", cls="android.widget.EditText", bounds="[88,650][304,750]")
        + "</hierarchy>"
    )
    automation.dump = lambda: xml
    node = automation.field_below_label("Name")
    assert node is not None and node.bounds == (88, 447, 632, 543)


def test_field_below_label_missing(automation):
    automation.dump = lambda: EMPTY_SCREEN
    assert automation.field_below_label("Name") is None


# ------------------------------------------------- regression: form detection
FORM_SCREEN = (
    "<hierarchy>"
    + _node("Add New Proxy", bounds="[84,150][400,190]")
    + _node("Name", bounds="[64,400][200,440]")
    + _node("Connection information", bounds="[64,350][300,380]")
    + _node("Connection type", bounds="[64,355][275,389]")
    + _node("HTTP", bounds="[88,264][592,298]")
    + _node("Server IP", bounds="[64,355][169,389]")
    + "</hierarchy>"
)


def test_empty_list_is_not_mistaken_for_form(automation):
    """Regression: "Add New Proxy" (form title) must not match the
    "Add new proxy" button on the empty-list screen."""
    automation.dump = lambda: EMPTY_SCREEN
    assert automation._on_form(EMPTY_SCREEN) is False


def test_real_form_is_detected(automation):
    automation.dump = lambda: FORM_SCREEN
    assert automation._on_form(FORM_SCREEN) is True


def test_title_alone_does_not_count_as_form(automation):
    """Only the Server IP / Connection type markers identify the form."""
    title_only = "<hierarchy>" + _node("Add New Proxy") + "</hierarchy>"
    assert automation._on_form(title_only) is False