"""
HF Contact -- echo test and short messages to a Mercury station, over HF.

Runs on Windows and Linux with nothing but Python's own Tk. Needs Mercury
running on this computer with your radio, and nothing else using it
(close Winlink Express / Pat first).
"""

import json
import os
import queue
import sys
import threading
import time
import webbrowser
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from . import __version__
from . import echo_service as es
from . import mercury_version as mv
from . import status as station_status
from .session import MAX_MESSAGE, compose_message, echo_summary, run_session, valid_call

DEFAULT_STATION = {
    "call": "AG7JC",
    "freq": "14.105 MHz USB (dial)",
    "note": "Listening times and news: ag7jc.com",
}

BG, PANEL, FIELD, FG, DIM = "#12161a", "#1a1f24", "#0c0f12", "#cfd8dc", "#7d8a94"
TEAL, ORANGE, GREEN, RED, GRAY = "#00d9d9", "#ff9500", "#33cc66", "#ff4444", "#5a6570"


def config_dir():
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
        return base / "HF Contact"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hf-contact"


class Store:
    def __init__(self):
        self.dir = config_dir()
        self.settings_path = self.dir / "settings.json"
        self.history_path = self.dir / "history.json"
        self.settings = {"mycall": "", "grid": "", "name": "", "host": "127.0.0.1", "port": 8300,
                         "station": dict(DEFAULT_STATION), "status_url": station_status.DEFAULT_URL,
                         # echo service (never starts by itself: the switch is always off at launch)
                         "echo_schedule": "always", "echo_from": 18, "echo_to": 22, "echo_dial": "",
                         "echo_rules_ok": False, "echo_listening": False}
        self.history = []
        for path, attr in ((self.settings_path, "settings"), (self.history_path, "history")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if attr == "settings":
                    self.settings.update(data)
                else:
                    self.history = data[-200:]
            except (OSError, ValueError):
                pass

    def save(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
            self.history_path.write_text(json.dumps(self.history[-200:], indent=1), encoding="utf-8")
        except OSError:
            pass


class App:
    def __init__(self, root):
        self.root = root
        self.store = Store()
        self.events = queue.Queue()
        self.cancel = None
        self.busy = False
        self.svc = None                 # the running EchoService, if any
        self.svc_token = None
        self.svc_retry_at = 0
        self.svc_state = "off"
        root.title("HF Contact")
        root.configure(bg=BG)
        root.minsize(420, 560)
        self._style()
        self._build()
        self._load_fields()
        self._render_history()
        self.root.after(150, self._pump)
        self.root.after(300, self._refresh_live)
        self.root.after(1000, self._svc_tick)
        if self.store.settings.get("echo_listening"):
            self._svc_cleanup()
        root.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------ look

    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor="#262d33",
                     lightcolor=PANEL, darkcolor=PANEL, troughcolor=FIELD, focuscolor=TEAL)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Card.TLabel", background=PANEL, foreground=FG)
        st.configure("Dim.TLabel", background=BG, foreground=DIM)
        st.configure("CardDim.TLabel", background=PANEL, foreground=DIM)
        st.configure("Title.TLabel", background=BG, foreground=ORANGE, font=("TkFixedFont", 16, "bold"))
        st.configure("Call.TLabel", background=PANEL, foreground=TEAL, font=("TkFixedFont", 13, "bold"))
        st.configure("TEntry", foreground=FG, insertcolor=FG, padding=5)
        st.map("TEntry", fieldbackground=[("disabled", PANEL)])
        st.configure("TButton", background="#262d33", foreground=FG, padding=(12, 7), borderwidth=0)
        st.map("TButton", background=[("pressed", "#1b2026"), ("active", "#323a42"), ("disabled", PANEL)],
               foreground=[("disabled", GRAY)])
        st.configure("Go.TButton", background="#009a9a", foreground="white", font=("TkDefaultFont", 10, "bold"))
        st.map("Go.TButton", background=[("pressed", "#00797a"), ("active", "#00b3b3"), ("disabled", PANEL)],
               foreground=[("disabled", GRAY)])
        st.configure("Stop.TButton", background="#b71c1c", foreground="white")
        st.map("Stop.TButton", background=[("pressed", "#7f1010"), ("active", "#d32626")])
        st.configure("Card.TCheckbutton", background=PANEL, foreground=FG, font=("TkDefaultFont", 10, "bold"),
                     indicatorbackground=FIELD, indicatorforeground=TEAL)
        st.map("Card.TCheckbutton", background=[("active", PANEL)],
               indicatorbackground=[("selected", "#009a9a"), ("active", "#262d33")],
               indicatorforeground=[("selected", "white")])
        st.configure("Link.TButton", background=PANEL, foreground=TEAL, padding=(6, 2))
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
               background=[("readonly", "#262d33")], arrowcolor=[("readonly", FG)])
        st.map("TRadiobutton", background=[("active", BG)], indicatorbackground=[("selected", "#009a9a")])
        for opt, val in (("background", FIELD), ("foreground", FG), ("selectBackground", "#009a9a"),
                         ("selectForeground", "white")):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)
        st.map("Link.TButton", background=[("active", "#262d33")])

    def _build(self):
        pad = {"padx": 14}
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, pady=10)

        ttk.Label(outer, text="HF CONTACT", style="Title.TLabel").pack(anchor="w", **pad)
        ttk.Label(outer, text="Echo test and short messages over HF, through your own Mercury modem.",
                  style="Dim.TLabel", wraplength=520, justify="left").pack(anchor="w", **pad)

        # the station we call
        card = ttk.Frame(outer, style="Card.TFrame", padding=10)
        card.pack(fill="x", pady=(10, 6), **pad)
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Calling", style="CardDim.TLabel").pack(side="left")
        self.station_lbl = ttk.Label(top, text="", style="Call.TLabel")
        self.station_lbl.pack(side="left", padx=8)
        ttk.Button(top, text="Settings…", style="Link.TButton", command=self._settings).pack(side="right")
        self.station_info = ttk.Label(card, text="", style="CardDim.TLabel", wraplength=500, justify="left")
        self.station_info.pack(anchor="w", pady=(4, 0))
        live = ttk.Frame(card, style="Card.TFrame")
        live.pack(fill="x", pady=(6, 0))
        self.live_light = tk.Canvas(live, width=12, height=12, bg=PANEL, highlightthickness=0)
        self.live_light.pack(side="left")
        self.live_dot = self.live_light.create_oval(1, 1, 11, 11, fill=GRAY, outline="")
        self.live_lbl = ttk.Label(live, text="Checking whether the station is on the air…", style="CardDim.TLabel",
                                  wraplength=470, justify="left")
        self.live_lbl.pack(side="left", padx=6)
        self.live_state = "unknown"
        ver = ttk.Frame(card, style="Card.TFrame")
        ver.pack(fill="x", pady=(4, 0))
        self.ver_lbl = ttk.Label(ver, text="", style="CardDim.TLabel", wraplength=470, justify="left")
        self.ver_lbl.pack(anchor="w", padx=(18, 6))
        self.ver_link = ttk.Button(ver, text="Get Mercury \u2192", style="Link.TButton",
                                   command=lambda: webbrowser.open(mv.RELEASES_URL))
        self.local_mercury = ("unknown", None)
        self.station_mercury = None
        self.ver_kind = None

        # you
        me = ttk.Frame(outer)
        me.pack(fill="x", pady=4, **pad)
        self.call_var, self.grid_var, self.name_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for col, (label, var, width) in enumerate((("Your callsign", self.call_var, 12),
                                                    ("Grid (optional)", self.grid_var, 8),
                                                    ("Name (optional)", self.name_var, 14))):
            f = ttk.Frame(me)
            f.grid(row=0, column=col, sticky="we", padx=(0, 8))
            ttk.Label(f, text=label, style="Dim.TLabel").pack(anchor="w")
            ttk.Entry(f, textvariable=var, width=width).pack(fill="x")
            me.columnconfigure(col, weight=1 if col == 2 else 0)
        self.call_var.trace_add("write", lambda *_: self._upper(self.call_var))
        self.grid_var.trace_add("write", lambda *_: self._upper(self.grid_var))

        # actions
        acts = ttk.Frame(outer)
        acts.pack(fill="x", pady=(10, 4), **pad)
        self.echo_btn = ttk.Button(acts, text="Echo test", style="Go.TButton", command=self._echo)
        self.echo_btn.pack(side="left")
        self.check_btn = ttk.Button(acts, text="Check for replies", command=self._check)
        self.check_btn.pack(side="left", padx=8)
        self.cancel_btn = ttk.Button(acts, text="Hang up", style="Stop.TButton", command=self._cancel)

        # message
        ttk.Label(outer, text="Message", style="Dim.TLabel").pack(anchor="w", pady=(8, 2), **pad)
        self.msg = tk.Text(outer, height=5, wrap="word", bg=FIELD, fg=FG, insertbackground=FG,
                           relief="flat", highlightthickness=1, highlightbackground="#262d33",
                           highlightcolor=TEAL, padx=8, pady=6, font=("TkDefaultFont", 11))
        self.msg.pack(fill="x", **pad)
        self.msg.bind("<<Modified>>", self._count)
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(4, 0), **pad)
        self.count_lbl = ttk.Label(row, text=f"0 / {MAX_MESSAGE}", style="Dim.TLabel")
        self.count_lbl.pack(side="left")
        self.send_btn = ttk.Button(row, text="Send message", style="Go.TButton", command=self._send)
        self.send_btn.pack(side="right")
        ttk.Label(outer, text="Plain text over amateur radio: no business, no encryption, "
                              "and anyone can read it.", style="Dim.TLabel", wraplength=520,
                  justify="left").pack(anchor="w", pady=(2, 0), **pad)

        # your own echo service
        svc = ttk.Frame(outer, style="Card.TFrame", padding=(10, 6))
        svc.pack(fill="x", pady=(10, 0), **pad)
        self.svc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(svc, text="Run an echo service", variable=self.svc_var, style="Card.TCheckbutton",
                        command=self._svc_toggle).pack(anchor="w")
        self.svc_lbl = ttk.Label(svc, text="", style="CardDim.TLabel", wraplength=500, justify="left")
        self.svc_lbl.pack(anchor="w", pady=(2, 0))
        self.svc_warn = ttk.Label(svc, text="", style="CardDim.TLabel", wraplength=500, justify="left")
        self._svc_show("off")

        # status
        st = ttk.Frame(outer, style="Card.TFrame", padding=(10, 6))
        st.pack(fill="x", pady=(10, 6), **pad)
        self.light = tk.Canvas(st, width=14, height=14, bg=PANEL, highlightthickness=0)
        self.light.pack(side="left")
        self.dot = self.light.create_oval(2, 2, 12, 12, fill=GRAY, outline="")
        self.status_lbl = ttk.Label(st, text="Ready", style="Card.TLabel", wraplength=480, justify="left")
        self.status_lbl.pack(side="left", padx=8, fill="x", expand=True)
        self.signal_lbl = ttk.Label(outer, text="", style="Dim.TLabel")
        self.signal_lbl.pack(anchor="e", padx=14, pady=(0, 4))

        # history
        hist_head = ttk.Frame(outer)
        hist_head.pack(fill="x", **pad)
        ttk.Label(hist_head, text="History", style="Dim.TLabel").pack(side="left")
        self.log_btn = ttk.Button(hist_head, text="Modem log", style="Link.TButton", command=self._toggle_log)
        self.log_btn.pack(side="right")
        self.hist = tk.Text(outer, height=10, wrap="word", bg=FIELD, fg=FG, relief="flat",
                            highlightthickness=0, padx=8, pady=6, state="disabled", font=("TkDefaultFont", 10))
        self.hist.pack(fill="both", expand=True, pady=(2, 0), **pad)
        for tag, color in (("when", DIM), ("echo", TEAL), ("reply", GREEN), ("sent", ORANGE), ("err", RED),
                           ("service", TEAL)):
            self.hist.tag_configure(tag, foreground=color)
        self.modem_log = tk.Text(outer, height=7, wrap="none", bg=FIELD, fg=DIM, relief="flat",
                                 highlightthickness=0, padx=8, pady=6, state="disabled",
                                 font=("TkFixedFont", 9))
        self.log_shown = False

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _upper(var):
        v = var.get()
        if v != v.upper():
            var.set(v.upper())

    def _load_fields(self):
        s = self.store.settings
        self.call_var.set(s.get("mycall", ""))
        self.grid_var.set(s.get("grid", ""))
        self.name_var.set(s.get("name", ""))
        self._show_station()

    def _show_station(self):
        st = self.store.settings["station"]
        self.station_lbl.configure(text=st.get("call", "?"))
        parts = [p for p in (st.get("freq"), st.get("note")) if p]
        self.station_info.configure(text="  ·  ".join(parts))

    def _count(self, _e=None):
        n = len(self.msg.get("1.0", "end-1c"))
        self.count_lbl.configure(text=f"{n} / {MAX_MESSAGE}", foreground=RED if n > MAX_MESSAGE else DIM)
        self.msg.edit_modified(False)

    def _set_light(self, color):
        self.light.itemconfigure(self.dot, fill=color)

    def _add_history(self, kind, text, save=True):
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        if save:
            self.store.history.append({"when": when, "kind": kind, "text": text})
            self.store.save()
        self._write_history(when, kind, text)

    def _write_history(self, when, kind, text):
        label = {"echo": "ECHO", "reply": "REPLY", "sent": "SENT", "err": "PROBLEM",
                 "service": "ECHO SERVICE"}.get(kind, kind.upper())
        self.hist.configure(state="normal")
        # newest on top: insert the pieces at the start, last piece first
        self.hist.insert("1.0", f"{text}\n\n")
        self.hist.insert("1.0", f"{label}\n", kind)
        self.hist.insert("1.0", f"{when}  ", "when")
        self.hist.configure(state="disabled")

    def _render_history(self):
        for h in self.store.history:
            self._write_history(h["when"], h["kind"], h["text"])

    def _refresh_live(self):
        url = self.store.settings.get("status_url", "")
        if not self.busy:
            threading.Thread(target=lambda: self.events.put(("live", station_status.fetch(url))),
                             daemon=True).start()
            threading.Thread(target=lambda: self.events.put(("mercury", mv.local_version())),
                             daemon=True).start()
        self.root.after(120000, self._refresh_live)

    def _show_live(self, doc):
        state, text = station_status.describe(doc, call=self.store.settings["station"].get("call", "The station"))
        self.live_state = state
        self.live_light.itemconfigure(self.live_dot, fill={"listening": GREEN, "busy": ORANGE,
                                                           "off": RED}.get(state, GRAY))
        self.live_lbl.configure(text=text)
        self.station_mercury = mv.station_version(doc)
        self._show_version()

    def _show_version(self):
        call = self.store.settings["station"].get("call", "The station")
        res = mv.compare(self.local_mercury, self.station_mercury, call)
        self.ver_kind = res[0] if res else None
        self.ver_lbl.configure(text=res[1] if res else "",
                               foreground={"match": GREEN, "mismatch": ORANGE}.get(self.ver_kind, DIM))
        if self.ver_kind == "mismatch":
            self.ver_link.pack(anchor="w", padx=(12, 0))
        else:
            self.ver_link.pack_forget()

    def _go_anyway(self):
        """Ask before calling a station that says it's off the air or busy.
        Unknown (no internet) never stops anyone."""
        call = self.store.settings["station"]["call"]
        if self.live_state == "off":
            return messagebox.askyesno("HF Contact", f"{call} looks off the air right now, so nobody may "
                                       f"answer.\n\nCall anyway?")
        if self.live_state == "busy":
            return messagebox.askyesno("HF Contact", f"{call} is in a call with someone else right now.\n\n"
                                       f"Call anyway?")
        if self.ver_kind == "mismatch":
            return messagebox.askyesno("HF Contact", self.ver_lbl.cget("text") + "\n\nCall anyway?")
        return True

    def _toggle_log(self):
        if self.log_shown:
            self.modem_log.pack_forget()
        else:
            self.modem_log.pack(fill="x", padx=14, pady=(6, 0))
        self.log_shown = not self.log_shown

    def _modem_line(self, line):
        self.modem_log.configure(state="normal")
        self.modem_log.insert("end", line + "\n")
        self.modem_log.see("end")
        self.modem_log.configure(state="disabled")

    # ------------------------------------------------------------ actions

    def _ready(self):
        call = self.call_var.get().strip().upper()
        if not valid_call(call):
            messagebox.showwarning("Your callsign", "Enter your callsign first (letters and digits, "
                                   "optional -SSID). It's how the other station knows who called.")
            return None
        s = self.store.settings
        s.update(mycall=call, grid=self.grid_var.get().strip().upper(), name=self.name_var.get().strip())
        self.store.save()
        return {"mycall": call, "target": s["station"]["call"], "host": s.get("host", "127.0.0.1"),
                "port": int(s.get("port", 8300)), "grid": s["grid"]}

    def _echo(self):
        cfg = self._ready()
        if cfg:
            self._start(cfg, echo=True)

    def _check(self):
        cfg = self._ready()
        if cfg:
            self._start(cfg)

    def _send(self):
        text = self.msg.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Message", "Type a message first.")
            return
        if len(text) > MAX_MESSAGE:
            messagebox.showwarning("Message", f"That's {len(text)} characters -- keep it under {MAX_MESSAGE}.")
            return
        cfg = self._ready()
        if cfg:
            msg = compose_message(text, self.store.settings.get("name", ""), cfg["grid"])
            self._start(cfg, message=msg, typed=text)

    def _start(self, cfg, echo=False, message=None, typed=None):
        if self.busy:
            return
        if self.svc and self.svc.linked_with:
            messagebox.showinfo("HF Contact", f"{self.svc.linked_with} is linked to your echo service right "
                                f"now. Try again when they're done (a minute or two at most).")
            return
        if not self._go_anyway():
            return
        self.busy = True
        svc = self._svc_stop("paused")      # one program on Mercury at a time
        self.cancel = threading.Event()
        for b in (self.echo_btn, self.check_btn, self.send_btn):
            b.state(["disabled"])
        self.cancel_btn.pack(side="right")
        self.signal_lbl.configure(text="")
        self._set_light(ORANGE)
        self._pending_typed = typed

        def work():
            if svc:
                svc.join(15)                 # let it hang up and let go of Mercury
            r = run_session(cfg, echo=echo, message=message, cancel=self.cancel,
                            progress=lambda k, t: self.events.put((k, t)))
            self.events.put(("done", r))
        threading.Thread(target=work, daemon=True).start()

    def _cancel(self):
        if self.cancel:
            self.cancel.set()
            self.status_lbl.configure(text="Hanging up…")

    # ------------------------------------------------------------ events

    def _pump(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                if kind == "line":
                    self._modem_line(val)
                elif kind in ("status", "info"):
                    self.status_lbl.configure(text=val)
                elif kind == "linked":
                    self.status_lbl.configure(text=val)
                    self._set_light(GREEN)
                elif kind == "echo":
                    self._set_light(TEAL)
                elif kind == "live":
                    self._show_live(val)
                elif kind == "mercury":
                    self.local_mercury = val
                    self._show_version()
                elif kind == "done":
                    self._done(val)
                elif kind.startswith("svc-"):
                    self._svc_event(kind, val)
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _done(self, r):
        self.busy = False
        self.root.after(500, self._svc_tick)    # back to listening, if it's on
        threading.Thread(target=lambda: self.events.put(("live", station_status.fetch(
            self.store.settings.get("status_url", "")))), daemon=True).start()
        for b in (self.echo_btn, self.check_btn, self.send_btn):
            b.state(["!disabled"])
        self.cancel_btn.pack_forget()
        heard = []
        if r.heard_snr is not None:
            heard.append(f"you hear them {r.heard_snr:+.1f} dB")
        if r.bitrate:
            heard.append(f"{r.bitrate} bps")
        self.signal_lbl.configure(text=" · ".join(heard))

        if r.echo_reply:
            self._add_history("echo", r.echo_reply)
        if r.message_sent and r.linked and not r.error:
            self._add_history("sent", self._pending_typed or "(message)")
            self.msg.delete("1.0", "end")
        for name, body in r.replies:
            self._add_history("reply", body)
        if r.error:
            self._add_history("err", r.error)
            self._set_light(RED)
            self.status_lbl.configure(text="Didn't work -- see History")
        else:
            self._set_light(GRAY)
            got = []
            if r.echo_reply:
                got.append(f"echo: {echo_summary(r.echo_reply) or 'reply received'}")
            if r.replies:
                got.append(f"{len(r.replies)} repl{'y' if len(r.replies) == 1 else 'ies'}")
            if r.message_sent:
                got.append("message delivered")
            self.status_lbl.configure(text="Done -- " + (", ".join(got) if got else "nothing waiting for you"))

    # ------------------------------------------------------------ echo service

    def _svc_cfg(self):
        s = self.store.settings
        return {"mycall": s["mycall"], "host": s.get("host", "127.0.0.1"), "port": int(s.get("port", 8300))}

    def _svc_toggle(self):
        if not self.svc_var.get():
            self._svc_stop("off")
            return
        s = self.store.settings
        call = self.call_var.get().strip().upper() or s.get("mycall", "")
        if not valid_call(call):
            call = (simpledialog.askstring("Echo service", "Your callsign (the service answers calls to it):",
                                           parent=self.root) or "").strip().upper()
            if not valid_call(call):
                if call:
                    messagebox.showwarning("Echo service", "That isn't a callsign (letters and digits, "
                                           "optional -SSID).")
                self.svc_var.set(False)
                return
            self.call_var.set(call)
        s["mycall"] = call
        if not s.get("echo_rules_ok"):
            ok = messagebox.askokcancel("Echo service", (
                "With the echo service on, Mercury answers calls to " + call + " and sends echo replies "
                "by itself, with nobody at the radio.\n\n"
                "In the US that's automatic control (47 CFR 97.221): General class or higher, and inside "
                "the automatic-control data segments, or with someone monitoring.\n\n" + es.SEGMENT_NOTE +
                "\n\nIt only answers echo tests. Messages people send are ignored and never kept.\n\n"
                "Turn it on?"))
            if not ok:
                self.svc_var.set(False)
                return
            s["echo_rules_ok"] = True
        self.store.save()
        ok, text = es.segment_check(es.parse_mhz(s.get("echo_dial", "")))
        if ok is False:
            messagebox.showwarning("Echo service", text)
        self.svc_retry_at = 0
        self._svc_tick(reschedule=False)

    def _svc_tick(self, reschedule=True):
        """Start or stop the service to match the switch, the hours and
        whether we're making a call of our own."""
        s = self.store.settings
        want = self.svc_var.get() and not self.busy
        in_hours = es.in_schedule(s)
        running = self.svc and self.svc.running
        if want and in_hours and not running:
            if time.time() >= self.svc_retry_at:
                token = self.svc_token = object()     # events from an older run are told apart by this
                self.svc = es.EchoService(self._svc_cfg(),
                                          progress=lambda k, v, t=token: self.events.put((k, (t, v))))
                self.svc.start()
                self._svc_mark(True)
                self._svc_show("starting")
        elif want and not in_hours:
            if running:
                self._svc_stop("waiting")
            else:
                self._svc_show("waiting")
        elif not self.svc_var.get() and running:
            self._svc_stop("off")
        if reschedule:
            self.root.after(30000, self._svc_tick)

    def _svc_stop(self, state):
        """Stop the service (it hangs up on a caller first). Returns it, so
        the caller can wait for Mercury to be free."""
        svc, self.svc = self.svc, None
        if svc:
            svc.stop()
        self._svc_show(state if self.svc_var.get() else "off")
        return svc

    def _svc_event(self, kind, tagged):
        token, val = tagged
        current = token is not None and token is self.svc_token and self.svc is not None
        if kind in ("svc-status", "svc-linked", "svc-unlinked", "svc-error") and not current:
            return                  # a run we already stopped, still winding down
        if kind == "svc-cleanup":
            if val:
                self._svc_mark(False)
                self._add_history("service", "The echo service didn't shut down properly last time, so Mercury "
                                  "may have kept answering calls. Its listening is now switched off.")
            return              # Mercury not running or busy: try again next start
        if kind == "svc-line":
            self._modem_line("[echo service] " + val)
        elif kind == "svc-status":
            self._svc_show("listening")
        elif kind == "svc-linked":
            self._svc_show("linked", val)
        elif kind == "svc-unlinked":
            self._svc_show("listening")
        elif kind == "svc-echo":
            snr = f"SNR {val['snr']:+.1f} dB" if val["snr"] is not None else "SNR not reported"
            bps = f"{val['bps']} bps" if val["bps"] else "speed not reported"
            self._add_history("service", f"Answered an echo test from {val['call']}: heard them {snr}, {bps}.")
        elif kind == "svc-ignored":
            self._add_history("service", f"{val} sent a message. Not kept (echo service only); they were told.")
        elif kind == "svc-refused":
            self._add_history("service", f"Hung up on {val}: over {es.LINKS_PER_HOUR} calls this hour.")
        elif kind == "svc-error":
            if self.svc_var.get() and not self.busy:
                self.svc_retry_at = time.time() + 60
                self._svc_show("error", val)
        elif kind == "svc-stopped":
            self._svc_mark(bool(self.svc and self.svc.running))
            if current:
                self.svc = None
                if self.svc_state != "error":
                    self._svc_show("off")
                    self.svc_var.set(False)

    def _svc_mark(self, listening):
        """Remember on disk whether a service has Mercury listening, so a
        crash can be cleaned up at the next start."""
        if self.store.settings.get("echo_listening") != listening:
            self.store.settings["echo_listening"] = listening
            self.store.save()

    def _svc_cleanup(self):
        """Last run ended with the service still on (crash, power cut, killed):
        Mercury may still be answering calls by itself. Switch that off."""
        def work():
            ok = es.listen_off(self._svc_cfg())
            self.events.put(("svc-cleanup", (None, ok)))
        threading.Thread(target=work, daemon=True).start()

    def _svc_show(self, state, detail=""):
        self.svc_state = state
        s = self.store.settings
        call = s.get("mycall") or "your callsign"
        when = es.describe_schedule(s)
        text, color = {
            "off": (f"Off. When on, Mercury answers echo tests to {call} by itself, so others can check "
                    f"their signal. Messages are never kept.", DIM),
            "starting": ("Opening Mercury…", DIM),
            "listening": (f"On: listening for calls to {call} ({when}).", GREEN),
            "linked": (f"{detail} is linked: answering their echo test…", TEAL),
            "waiting": (f"On, outside its hours ({when}). Mercury is left alone until then.", DIM),
            "paused": ("Paused for your call. It goes back to listening when you're done.", ORANGE),
            "error": (f"Couldn't listen: {detail} Trying again in a minute.", RED),
        }[state]
        self.svc_lbl.configure(text=text, foreground=color)
        if self.svc_var.get():
            ok, warn = es.segment_check(es.parse_mhz(s.get("echo_dial", "")))
            if ok is None:
                warn, wcolor = ("Set your dial frequency in Settings to check it against the US "
                                "automatic-control segments.", DIM)
            else:
                wcolor = GREEN if ok else ORANGE
            self.svc_warn.configure(text=warn, foreground=wcolor)
            self.svc_warn.pack(anchor="w", pady=(2, 0))
        else:
            self.svc_warn.pack_forget()

    # ------------------------------------------------------------ settings

    def _settings(self):
        s = self.store.settings
        win = tk.Toplevel(self.root, bg=BG)
        win.title("Settings")
        win.transient(self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        fields = [("Station to call", "call", s["station"].get("call", "")),
                  ("Its frequency (shown only)", "freq", s["station"].get("freq", "")),
                  ("Note (shown only)", "note", s["station"].get("note", "")),
                  ("Mercury address", "host", s.get("host", "127.0.0.1")),
                  ("Mercury TNC port", "port", str(s.get("port", 8300))),
                  ("Live status URL (optional)", "status_url", s.get("status_url", ""))]
        vars_ = {}
        for i, (label, key, val) in enumerate(fields):
            ttk.Label(frm, text=label, style="Dim.TLabel").grid(row=i, column=0, sticky="w", pady=4, padx=(0, 10))
            v = tk.StringVar(value=val)
            ttk.Entry(frm, textvariable=v, width=32).grid(row=i, column=1, sticky="we", pady=4)
            vars_[key] = v
        ttk.Label(frm, text="Mercury's TNC port is usually 8300 (the data port is the next one up).",
                  style="Dim.TLabel", wraplength=380).grid(row=len(fields), column=0, columnspan=2, sticky="w",
                                                          pady=(6, 10))

        # echo service
        eb = ttk.Frame(frm)
        eb.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="we", pady=(4, 10))
        ttk.Label(eb, text="ECHO SERVICE", style="Dim.TLabel", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        sched = tk.StringVar(value=s.get("echo_schedule", "always"))
        hrs = [f"{h:02d}:00" for h in range(24)]
        from_v = tk.StringVar(value=hrs[int(s.get("echo_from", 18)) % 24])
        to_v = tk.StringVar(value=hrs[int(s.get("echo_to", 22)) % 24])
        row1 = ttk.Frame(eb)
        row1.pack(anchor="w", pady=(4, 0))
        ttk.Radiobutton(row1, text="All the time", value="always", variable=sched).pack(side="left")
        row2 = ttk.Frame(eb)
        row2.pack(anchor="w", pady=(2, 0))
        ttk.Radiobutton(row2, text="Between", value="hours", variable=sched).pack(side="left")
        ttk.Combobox(row2, textvariable=from_v, values=hrs, width=6, state="readonly").pack(side="left", padx=4)
        ttk.Label(row2, text="and").pack(side="left")
        ttk.Combobox(row2, textvariable=to_v, values=hrs, width=6, state="readonly").pack(side="left", padx=4)
        ttk.Label(row2, text="(this computer's time)", style="Dim.TLabel").pack(side="left")
        row3 = ttk.Frame(eb)
        row3.pack(anchor="w", fill="x", pady=(6, 0))
        ttk.Label(row3, text="Your dial frequency, MHz (USB)", style="Dim.TLabel").pack(side="left")
        dial_v = tk.StringVar(value=s.get("echo_dial", ""))
        ttk.Entry(row3, textvariable=dial_v, width=12).pack(side="left", padx=8)
        check = ttk.Label(eb, text="", style="Dim.TLabel", wraplength=420, justify="left")
        check.pack(anchor="w", pady=(4, 0))
        ttk.Label(eb, text=es.SEGMENT_NOTE + " Your radio isn't tuned by HF Contact: this is only checked.",
                  style="Dim.TLabel", wraplength=420, justify="left").pack(anchor="w", pady=(4, 0))

        def check_dial(*_):
            ok, text = es.segment_check(es.parse_mhz(dial_v.get()))
            if ok is None and dial_v.get().strip():
                text, ok = "Not a frequency I can read (try 14.105).", False
            check.configure(text=text, foreground={True: GREEN, False: ORANGE}.get(ok, DIM))
        dial_v.trace_add("write", check_dial)
        check_dial()

        def save():
            call = vars_["call"].get().strip().upper()
            if not valid_call(call):
                messagebox.showwarning("Settings", "The station to call has to be a callsign.", parent=win)
                return
            try:
                port = int(vars_["port"].get())
            except ValueError:
                messagebox.showwarning("Settings", "The port has to be a number.", parent=win)
                return
            s["station"] = {"call": call, "freq": vars_["freq"].get().strip(), "note": vars_["note"].get().strip()}
            s["host"] = vars_["host"].get().strip() or "127.0.0.1"
            s["port"] = port
            s["status_url"] = vars_["status_url"].get().strip()
            s["echo_schedule"] = sched.get()
            s["echo_from"], s["echo_to"] = int(from_v.get()[:2]), int(to_v.get()[:2])
            s["echo_dial"] = dial_v.get().strip()
            self.store.save()
            self._svc_show(self.svc_state)
            self._svc_tick(reschedule=False)
            self._show_station()
            win.destroy()
            self._show_live(None)
            threading.Thread(target=lambda: self.events.put(("live", station_status.fetch(s["status_url"]))),
                             daemon=True).start()

        def reset():
            for k, v in DEFAULT_STATION.items():
                vars_[k].set(v)
            vars_["host"].set("127.0.0.1")
            vars_["port"].set("8300")
            vars_["status_url"].set(station_status.DEFAULT_URL)

        btns = ttk.Frame(frm)
        btns.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="Defaults", command=reset).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Save", style="Go.TButton", command=save).pack(side="left")
        ttk.Label(frm, text=f"HF Contact {__version__}", style="Dim.TLabel").grid(
            row=len(fields) + 3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _quit(self):
        if self.busy and not messagebox.askyesno("HF Contact", "A call is in progress. Hang up and quit?"):
            return
        if self.cancel:
            self.cancel.set()
        svc = self._svc_stop("off")
        if svc:
            svc.join(12)                     # hang up on a caller properly before we go
            if not svc.running:
                self._svc_mark(False)
        self.root.destroy()


def resource(name):
    """A file shipped with the app: next to the package, or inside the .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


def set_icon(root):
    try:
        img = tk.PhotoImage(file=str(resource("icon.png")))
        root.iconphoto(True, img)
        root._icon = img                     # keep a reference, or Tk drops it
    except (tk.TclError, OSError):
        pass


def main():
    # className sets the window's WM_CLASS ("hf-contact" / "Hf-contact"), which
    # the desktop file's StartupWMClass matches, so the dock shows our icon
    root = tk.Tk(className="hf-contact")
    set_icon(root)
    try:
        root.tk.call("tk", "scaling", max(1.0, root.winfo_fpixels("1i") / 72.0))
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
