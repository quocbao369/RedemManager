"""Read the redemption URL straight out of the Bing WebView over CDP."""
import base64
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

ADB = r"D:\ProgramFiles\LDPlayer\adb.exe"
DEFAULT_SERIAL = "emulator-5564"
PORT = 9222
# One forward per device so several emulators can be inspected side by side.
_PORTS = {}


def port_for(serial: str) -> int:
    if serial not in _PORTS:
        _PORTS[serial] = PORT + len(_PORTS)
    return _PORTS[serial]

JS = r"""
(function () {
    var a = document.querySelector('a[href*="gift.shopmygiftcards.com"]');
    var out = { giftUrl: a ? a.href : null, texts: [] };
    var src = document.body.innerText;
    out.texts = src.split('\n').map(function (x) { return x.trim(); })
                  .filter(function (x) { return x.length; }).slice(0, 25);
    return JSON.stringify(out);
})()
"""


def _adb(*args, timeout=60):
    return subprocess.run([ADB, *args], capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def find_socket(serial: str = DEFAULT_SERIAL):
    """Return the WebView devtools socket name, without the leading '@'.

    /proc/net/unix lists it as '@webview_devtools_remote_<pid>' but
    `adb forward` wants 'localabstract:webview_devtools_remote_<pid>'.
    """
    out = _adb("-s", serial, "shell", "cat", "/proc/net/unix").stdout
    for line in out.splitlines():
        if "webview_devtools_remote_" in line:
            return line.strip().split()[-1].lstrip("@")
    return None


def list_pages(serial: str = DEFAULT_SERIAL):
    port = port_for(serial)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                    timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        return []


def connect(serial: str = DEFAULT_SERIAL):
    name = find_socket(serial)
    if not name:
        return None
    port = port_for(serial)
    _adb("-s", serial, "forward", "--remove-all")
    _adb("-s", serial, "forward", f"tcp:{port}", f"localabstract:{name}")
    return name


def _read_exact(sock, count):
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf.decode("utf-8", errors="replace")


def _send_ws(sock, payload: bytes):
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 1 << 16:
        header.append(0x80 | 126)
        header += length.to_bytes(2, "big")
    else:
        header.append(0x80 | 127)
        header += length.to_bytes(8, "big")
    mask = os.urandom(4)
    header += mask
    sock.send(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def evaluate(expression: str, timeout: int = 30, serial: str = DEFAULT_SERIAL):
    pages = list_pages(serial)
    if not pages:
        return None
    port = port_for(serial)
    path = pages[0]["webSocketDebuggerUrl"].split(f":{port}", 1)[1]

    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.send((
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        handshake = b""
        while b"\r\n\r\n" not in handshake:
            chunk = s.recv(4096)
            if not chunk:
                break
            handshake += chunk

        _send_ws(s, json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True,
                       "awaitPromise": True},
        }).encode())

        deadline = time.time() + timeout
        while time.time() < deadline:
            h = s.recv(2)
            if not h:
                break
            ln = h[1] & 0x7F
            if ln == 126:
                ln = int.from_bytes(s.recv(2), "big")
            elif ln == 127:
                ln = int.from_bytes(s.recv(8), "big")
            msg = json.loads(_read_exact(s, ln))
            if msg.get("id") == 1:
                return msg.get("result", {}).get("result", {}).get("value")
    except Exception:
        return None
    finally:
        s.close()
    return None


def read_voucher_url(serial=DEFAULT_SERIAL):
    connect(serial)
    raw = evaluate(JS, serial=serial)
    if not raw:
        return None, []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, []
    return data.get("giftUrl"), data.get("texts", [])


def voucher_url(serial=DEFAULT_SERIAL):
    """Return just the redemption URL from the current page."""
    return read_voucher_url(serial)[0]


ORDERS_JS = r"""
(function () {
    var text = document.body.innerText;
    var lines = text.split('\n').map(function (s) { return s.trim(); })
                      .filter(function (s) { return s.length; });
    var dateRe = /(\d{1,2})\/(\d{1,2})\/(\d{4})/;
    var orders = [];
    for (var i = 0; i < lines.length; i++) {
        var m = lines[i].match(dateRe);
        if (!m) continue;
        var pts = null, product = null, orderNo = null;
        for (var j = i - 1; j >= 0 && j > i - 8; j--) {
            var p = lines[j];
            if (pts === null && /^[\d.,\s]+(pts|điểm)?$/i.test(p) && /[.,]/.test(p)) {
                var d = p.replace(/\D/g, ''); if (d) { pts = parseInt(d, 10); continue; }
            }
            if (product === null && p.length > 3 && p.length < 70 &&
                !dateRe.test(p) && !/^(pts|điểm|\||Order history|Your orders|Redeem|Dashboard|Earn|About)$/i.test(p)) {
                product = p;
            }
        }
        // The order number is rendered AFTER the date line.
        for (var k = i + 1; k < lines.length && k < i + 4; k++) {
            var q = lines[k];
            if (/^Order no\.\s*\S+/i.test(q)) { orderNo = q.split(/\s+/).pop(); break; }
            if (/^Số đơn hàng/i.test(q)) { orderNo = q.replace(/^Số đơn hàng/i, '').trim(); break; }
        }
        if (dateRe.test(lines[i])) {
            orders.push({
                date: m[0],
                points: pts,
                product: product,
                orderNo: orderNo,
                raw: lines.slice(Math.max(0, i - 6), i + 3)
            });
        }
    }
    return JSON.stringify({ orders: orders, lines: lines.slice(0, 10) });
})()
"""


CLICK_VIEW_DETAIL_JS = r"""
(function () {
    var els = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .filter(function (e) { return (e.innerText || '').trim() === 'View detail'; });
    if (!els.length) return 'NOT_FOUND';
    els[0].scrollIntoView({ block: 'center' });
    els[0].click();
    return 'CLICKED ' + els.length;
})()
"""


def click_view_detail(serial=DEFAULT_SERIAL):
    """Click the n-th "View detail" button from inside the page.

    Doing this over CDP avoids depending on accessibility bounds, which go
    negative when the button is only half visible.
    """
    connect(serial)
    return evaluate(CLICK_VIEW_DETAIL_JS, serial=serial)


def read_orders(serial=DEFAULT_SERIAL):
    """Extract every order on the current order-history page from the DOM."""
    connect(serial)
    raw = evaluate(ORDERS_JS, serial=serial)
    if not raw:
        return []
    try:
        return json.loads(raw).get("orders", [])
    except json.JSONDecodeError:
        return []