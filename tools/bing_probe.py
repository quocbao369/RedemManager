"""Exploration helper: dump UI text + screenshot of the Bing app."""
import re
import subprocess
import sys
import time

ADB = r"C:\ProgramFiles\LDPlayer\adb.exe"
SERIAL = "emulator-5564"
OUT = r"C:\Users\THANHVIET\AppData\Local\Temp\opencode"


def sh(*args, timeout=90):
    return subprocess.run([ADB, "-s", SERIAL, "shell", *[str(a) for a in args]],
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def dump():
    sh("uiautomator", "dump", "/sdcard/_x.xml", timeout=120)
    return sh("cat", "/sdcard/_x.xml").stdout or ""


def report(tag, shot=True):
    xml = dump()
    pkg = re.search(r'package="([^"]+)"', xml)
    print(f"\n===== {tag} =====")
    print("package:", pkg.group(1) if pkg else "?")
    seen = set()
    for m in re.finditer(r'<node\s([^>]*?)/?>', xml):
        a = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', m.group(1)))
        t = a.get("text", "")
        if not t or t in seen:
            continue
        seen.add(t)
        print(f"  {t!r:60} {a.get('bounds'):26} {a.get('class')}")
    if shot:
        sh("screencap", "-p", "/sdcard/_s.png")
        subprocess.run([ADB, "-s", SERIAL, "pull", "/sdcard/_s.png",
                        rf"{OUT}\{tag}.png"], capture_output=True, timeout=120)
        print(f"  -> screenshot: {OUT}\\{tag}.png")
    return xml


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        report(sys.argv[2] if len(sys.argv) > 2 else "screen")
    elif cmd == "tap":
        sh("input", "tap", sys.argv[2], sys.argv[3]); time.sleep(4)
    elif cmd == "type":
        import shlex
        sh("input", "text", shlex.quote(sys.argv[2])); time.sleep(2)
    elif cmd == "back":
        sh("input", "keyevent", "4"); time.sleep(3)
    elif cmd == "dump":
        print(dump())