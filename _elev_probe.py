import ctypes
import ctypes.wintypes as wt
import http.server
import os
import socketserver
import subprocess
import tempfile
import threading
import time
import shutil
import traceback

REPORT = os.path.join(tempfile.gettempdir(), "elev_report.txt")
lines = []


def say(text):
    lines.append(text)
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>CoainProbe</title></head><body><h1>p</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def titles():
    out = []
    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(proc(collect), 0)
    return out


def main():
    say("elevated: %s" % bool(ctypes.windll.shell32.IsUserAnAdmin()))
    say("user: %s" % os.environ.get("USERNAME"))
    say("temp: %s" % tempfile.gettempdir())

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port
    say("server: " + url)

    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    say("edge exists: %s" % os.path.isfile(edge))

    variants = [("base", []), ("nosandbox", ["--no-sandbox"])]
    for name, extra in variants:
        profile = tempfile.mkdtemp(prefix="elevprobe-")
        errfile = os.path.join(tempfile.gettempdir(), "elevprobe-%s.err" % name)
        cmd = [edge, "--app=" + url, "--user-data-dir=" + profile,
               "--no-first-run", "--no-default-browser-check",
               "--window-size=900,600", "--disable-features=Translate,msEdgeSidebar"] + extra
        say("--- %s ---" % name)
        say("cmd: " + " ".join(cmd))
        started = time.time()
        with open(errfile, "wb") as ef:
            child = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=ef)
            found = None
            for _ in range(24):
                time.sleep(0.5)
                hit = [t for t in titles() if "Coain" in t[1] or ("127.0.0.1:%d" % port) in t[1]]
                if hit:
                    found = hit[0]
                    break
                if child.poll() is not None and time.time() - started > 3:
                    break
        say("child poll: %s (%.1fs)" % (child.poll(), time.time() - started))
        items = 0
        try:
            items = len(os.listdir(profile))
        except Exception:
            pass
        say("profile items: %d" % items)
        try:
            with open(errfile, "rb") as ef:
                err = ef.read(600).decode("utf-8", "replace").strip()
            say("stderr: " + (err if err else "(empty)"))
        except Exception:
            pass
        if found:
            say("WINDOW: %r" % found[1])
            ctypes.windll.user32.PostMessageW(found[0], 0x0010, 0, 0)
            time.sleep(2)
            say("after close, still there: %s" % any(h == found[0] for h, _ in titles()))
        else:
            say("NO WINDOW")
        try:
            child.kill()
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    say("DONE")


try:
    main()
except Exception:
    say("EXCEPTION:\n" + traceback.format_exc())
