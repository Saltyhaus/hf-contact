"""Screenshots of the echo service: the app listens (station B), another
HF Contact calls it over fake air (station A).
usage: python3 tests/screens_service.py OUTDIR"""
import os, subprocess, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
out = sys.argv[1]
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
from tests.fake_air import FakeAir
import tkinter as tk
from hf_contact import app as appmod
from hf_contact.session import run_session

air = FakeAir(18800, 18810, snr_a=5.1, snr_b=3.4, bitrate=1150)
root = tk.Tk(className="hf-contact")
appmod.set_icon(root)
root.geometry("560x940+0+0")
a = appmod.App(root)
s = a.store.settings
s.update(port=18810, status_url="", echo_rules_ok=True, echo_dial="14.105")
a.call_var.set("AG7JC-5")
results = []

def shot(name, win="root", geom="560x940+0+0"):
    root.update(); time.sleep(0.4); root.update()
    subprocess.run(["import", "-window", "root", "-crop", geom, f"{out}/{name}.png"])

def caller():
    cfg = {"mycall": "K1ABC", "target": "AG7JC-5", "host": "127.0.0.1", "port": 18800, "grid": "FN42"}
    results.append(run_session(cfg, echo=True, message="Can you pass this on?", connect_timeout=20,
                               idle_after=3, echo_timeout=20))

def step1():
    a.svc_var.set(True)
    a._svc_toggle()
    root.after(2500, step2)

def step2():
    shot("service-listening")
    t = threading.Thread(target=caller, daemon=True); t.start()
    def wait():
        if t.is_alive():
            root.after(300, wait)
        else:
            root.after(1500, step3)
    wait()

def step3():
    shot("service-answered")
    print("caller got:", results[0].echo_reply, results[0].replies, results[0].error)
    a._settings()
    root.after(800, step4)

def step4():
    win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
    win.geometry("+600+0")
    root.update(); time.sleep(0.3); root.update()
    x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
    subprocess.run(["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", f"{out}/service-settings.png"])
    win.destroy()
    s["echo_dial"] = "14.074"
    a._svc_show(a.svc_state)
    shot("service-warning")
    a._quit()

root.after(800, step1)
root.mainloop()
print("svc after quit:", a.svc)
