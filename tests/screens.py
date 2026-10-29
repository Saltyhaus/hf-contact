"""Drive the real window against fake air + a real connector, take screenshots.
usage: CONNECTOR=... python3 tests/screens.py OUTDIR"""
import os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out = sys.argv[1]
home = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = home
from tests.fake_air import FakeAir
import tkinter as tk
from hf_contact import app as appmod

w = tempfile.mkdtemp(); spool_out = w + "/o/"; spool_in = w + "/i/"
os.makedirs(spool_out + "K1ABC"); os.makedirs(spool_in)
air = FakeAir(18700, 18710, snr_a=6.2, snr_b=4.0, bitrate=1400)
conn = subprocess.Popen([os.environ["CONNECTOR"], "-x", "mercury", "-i", spool_out, "-o", spool_in, "-c", "AG7JC",
                         "-d", "AG7JC-2,AG7JC-3", "-a", "127.0.0.1", "-p", "18700", "-t", "600", "-g"],
                        stdout=subprocess.DEVNULL, stderr=open(w + "/log", "w"))
air.a.ready.wait(10)

import hf_contact.session as sess
_orig = sess.run_session
appmod.run_session = lambda cfg, **kw: _orig(cfg, idle_after=3, connect_timeout=15, echo_timeout=20, **kw)

import http.server, json as _json, threading as _th
class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        now = int(time.time())
        body = _json.dumps({"callsign": "AG7JC", "status": "listening", "freq_hz": 14105000, "mode": "PKTUSB",
                            "updated_unix": now - 30, "stale_after": 300, "recent_echoes": []}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
_srv = http.server.HTTPServer(("127.0.0.1", 18799), _H)
_th.Thread(target=_srv.serve_forever, daemon=True).start()

root = tk.Tk()
root.geometry("560x860+0+0")
a = appmod.App(root)
a.store.settings["port"] = 18710
a.store.settings["status_url"] = "http://127.0.0.1:18799/station.json"
root.after(100, a._refresh_live)
a.call_var.set("k1abc"); a.grid_var.set("fn42"); a.name_var.set("Pat")

def shot(name):
    root.update(); time.sleep(0.3); root.update()
    subprocess.run(["import", "-window", "root", "-crop", "560x860+0+0", f"{out}/{name}.png"])

def wait_idle(then, t=0):
    if a.busy and t < 120:
        root.after(500, wait_idle, then, t + 1)
    else:
        then()

def step1():
    a._echo()
    root.after(1800, lambda: (shot("2-linked"), wait_idle(step2)))

def step2():
    shot("3-echo")
    a.msg.insert("1.0", "Hi Joshua -- found your site, nice work on the amp. Hearing you fine in Boston.")
    a._count()
    shot("4-typed")
    # Joshua answers from the HF dashboard (lands in the guest's outbox folder)
    p = spool_out + "K1ABC/reply_7.txt"
    open(p, "w").write("type: reply\ncallsign: K1ABC\n\nThanks Pat! 73 from San Diego -- Joshua")
    old = time.time() - 10; os.utime(p, (old, old))
    a._send()
    wait_idle(step3)

def step3():
    shot("5-sent-and-reply")
    print("inbox:", os.listdir(spool_in))
    conn.terminate(); root.destroy()

root.after(800, lambda: (shot("1-start"), step1()))
root.mainloop()
