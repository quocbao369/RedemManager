import json
import sys
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from database import Database
from services import proxy_rotation
from services.proxy_rotation import (
    parse_proxy_string, rotate_proxy, set_setting,
    SETTING_ROTATION_URL, SETTING_GLOBAL_PROXY,
)


class _Handler(BaseHTTPRequestHandler):
    payload = {}
    status_code = 200

    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def clean_settings():
    with Database.transaction() as conn:
        conn.execute("DELETE FROM app_settings")
    yield


def _url(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}/rotate"


# --------------------------------------------------------------- parsing
def test_parse_proxy_string_full():
    p = parse_proxy_string("180.93.1.107:1080:noahevans535:mzq3odkxnzu2")
    assert p == {
        "host": "180.93.1.107",
        "port": "1080",
        "username": "noahevans535",
        "password": "mzq3odkxnzu2",
    }


def test_parse_proxy_string_with_scheme():
    p = parse_proxy_string("http://1.2.3.4:8080:user:pass")
    assert p["host"] == "1.2.3.4" and p["port"] == "8080"


def test_parse_proxy_string_password_with_colon():
    p = parse_proxy_string("1.2.3.4:8080:user:pa:ss")
    assert p["password"] == "pa:ss"


def test_parse_proxy_string_invalid():
    assert parse_proxy_string("") is None
    assert parse_proxy_string("nonsense") is None


def test_parse_proxy_without_credentials():
    p = parse_proxy_string("1.2.3.4:8080")
    assert p["username"] == "" and p["password"] == ""


# --------------------------------------------------------------- settings
def test_settings_roundtrip():
    set_setting(SETTING_ROTATION_URL, "http://example.com/rotate")
    set_setting(SETTING_GLOBAL_PROXY, "1.2.3.4:8080:u:p")
    assert proxy_rotation.get_setting(SETTING_ROTATION_URL) == "http://example.com/rotate"
    assert proxy_rotation.get_global_proxy()["host"] == "1.2.3.4"


# --------------------------------------------------------------- rotation
def test_rotate_success(server):
    _Handler.payload = {
        "status": "success",
        "message": "Xoay proxy thanh cong",
        "proxy": "180.93.1.107:1080:noahevans535:mzq3odkxnzu2",
        "ip": "42.119.227.144",
        "lastRotate": "2026-10-14T12:49:41.632Z",
        "timeRemaining": 60,
    }
    set_setting(SETTING_ROTATION_URL, _url(server))
    r = rotate_proxy()
    assert r.success is True
    assert r.proxy == "180.93.1.107:1080:noahevans535:mzq3odkxnzu2"
    assert r.ip == "42.119.227.144"


def test_rotate_without_url():
    r = rotate_proxy()
    assert r.success is False
    assert "rotation" in r.message.lower()


def test_rotate_failure_status(server):
    _Handler.payload = {"status": "error", "message": "het slot", "timeRemaining": 0}
    set_setting(SETTING_ROTATION_URL, _url(server))
    r = rotate_proxy(max_attempts=1)
    assert r.success is False