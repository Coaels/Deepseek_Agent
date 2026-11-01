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


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>CoainProbe</title></head><body><h1>probe</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
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


srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % port
print("server:", url, flush=True)

edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
profile = tempfile.mkdtemp(prefix="coain-")
print("profile:", profile, flush=True)

started = time.time()
child = subprocess.Popen(
    [edge, "--app=" + url, "--user-data-dir=" + profile,
     "--no-first-run", "--no-default-browser-check",
     "--window-size=1120,780", "--disable-features=Translate,msEdgeSidebar"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

marker = "127.0.0.1:%d" % port
seen = None
deadline = time.time() + 30
while time.time() < deadline:
    time.sleep(1)
    hit = [item for item in titles() if "Coain" in item[1] or marker in item[1]]
    if hit:
        seen = hit[0]
        print("WINDOW after %.1fs: %r" % (time.time() - started, seen[1]), flush=True)
        break
    if child.poll() is not None and time.time() - started > 4:
        print("child exited early rc=%s at %.1fs" % (child.returncode, time.time() - started), flush=True)
        break

if not seen:
    print("RESULT: no window", flush=True)
    shutil.rmtree(profile, ignore_errors=True)
    raise SystemExit(1)

ctypes.windll.user32.PostMessageW(seen[0], 0x0010, 0, 0)
print("WM_CLOSE sent", flush=True)

gone_at = None
for _ in range(20):
    time.sleep(0.5)
    if not any(h == seen[0] for h, _ in titles()):
        gone_at = time.time() - started
        print("window gone at %.1fs" % gone_at, flush=True)
        break

print("RESULT:", "OK" if gone_at else "window still open", flush=True)

time.sleep(2)
left = [t for h, t in titles() if "Coain" in t or marker in t]
print("leftover windows:", left, flush=True)

shutil.rmtree(profile, ignore_errors=True)
print("profile removed:", not os.path.exists(profile), flush=True)
