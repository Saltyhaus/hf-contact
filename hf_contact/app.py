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
from datetime import timezone
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__
from . import adif
from . import listing
from . import updates
from . import notify
from . import rig
from . import stations as station_list
from . import echo_service as es
from . import mercury_version as mv
from . import scroll
from . import themes
from . import status as station_status
from .session import MAX_MESSAGE, compose_message, echo_summary, run_session, valid_call

DEFAULT_STATION = {
    "call": "AG7JC",
    "freq": "14.105 MHz USB (dial)",
    "note": "Listening times and news: ag7jc.com",
}

# colours: set from the theme picked in Settings (themes.py), before the window is built
BG = PANEL = FIELD = FG = DIM = TEAL = ORANGE = GREEN = RED = GRAY = ""
BORDER = BTN = BTN_ACTIVE = BTN_PRESS = GO = GO_ACTIVE = GO_PRESS = GO_FG = ""
STOP = STOP_ACTIVE = STOP_PRESS = STOP_FG = ""


def apply_theme(name):
    """Point the colour names at a theme's palette. TEAL and ORANGE are the
    accent and the warm colour (their names are from the first, teal look)."""
    pal = themes.get(name)
    g = globals()
    for k, v in pal.items():
        if k.isupper() and isinstance(v, str):
            g[k] = v
    g["TEAL"], g["ORANGE"] = pal["ACCENT"], pal["WARM"]


apply_theme(themes.DEFAULT)



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
                         "echo_rules_ok": False, "echo_listening": False,
                         "echo_notify": True, "rig_addr": "", "stations_url": station_list.DEFAULT_URL,
                         "update_checked": 0, "text_size": "normal", "window": "", "theme": themes.DEFAULT}
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
        self.rig_mhz = None             # the dial as rigctld reports it, when set up
        self.stations = []              # the published station list (or the last one saved)
        self.list_submit = None         # where "List my station" requests go, from the list
        self.list_cached_at = None      # set when the list is the saved copy (no internet)
        self.asked = set()              # warnings already asked about; then just shown
        self._list_request_pending = False
        root.title("HF Contact")
        root.configure(bg=BG)
        root.minsize(420, 360)
        self._style()
        self._build()
        self._size_window()
        self._load_fields()
        self._render_history()
        self.root.after(150, self._pump)
        self.root.after(300, self._refresh_live)
        self.root.after(1000, self._svc_tick)
        self._fetch_stations()
        self.root.after(4000, self._check_update)
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
        st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
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
        st.configure("TButton", background=BTN, foreground=FG, padding=(12, 7), borderwidth=0)
        st.map("TButton", background=[("pressed", BTN_PRESS), ("active", BTN_ACTIVE), ("disabled", PANEL)],
               foreground=[("disabled", GRAY)])
        st.configure("Go.TButton", background=GO, foreground=GO_FG, font=("TkDefaultFont", 10, "bold"))
        st.map("Go.TButton", background=[("pressed", GO_PRESS), ("active", GO_ACTIVE), ("disabled", PANEL)],
               foreground=[("disabled", GRAY)])
        st.configure("Stop.TButton", background=STOP, foreground=STOP_FG)
        st.map("Stop.TButton", background=[("pressed", STOP_PRESS), ("active", STOP_ACTIVE)])
        st.configure("Card.TCheckbutton", background=PANEL, foreground=FG, font=("TkDefaultFont", 10, "bold"),
                     indicatorbackground=FIELD, indicatorforeground=TEAL)
        st.map("Card.TCheckbutton", background=[("active", PANEL)],
               indicatorbackground=[("selected", GO), ("active", BTN)],
               indicatorforeground=[("selected", GO_FG)])
        st.configure("Link.TButton", background=PANEL, foreground=TEAL, padding=(6, 2))
        st.configure("Update.TButton", background=BG, foreground=TEAL, padding=(0, 2))
        st.map("Update.TButton", background=[("active", BG)], foreground=[("active", FG)])
        st.configure("TCombobox", background=BTN, arrowcolor=FG)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
               background=[("readonly", BTN)], arrowcolor=[("readonly", FG)])
        st.map("TRadiobutton", background=[("active", BG)], indicatorbackground=[("selected", GO)])
        st.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", GO)])
        for opt, val in (("background", FIELD), ("foreground", FG), ("selectBackground", GO),
                         ("selectForeground", GO_FG)):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)
        st.map("Link.TButton", background=[("active", BTN)])

    def _size_window(self):
        """Fit the screen: a 768-pixel laptop can't show it all, so the
        window scrolls rather than cutting off the bottom."""
        self.root.update_idletasks()
        want_w = self.area.body.winfo_reqwidth()
        want_h = self.area.needed_height()
        scroll.fit_to_screen(self.root, want_w, want_h, saved=self.store.settings.get("window"))

    def _build(self):
        pad = {"padx": 14}
        self.area = scroll.ScrollArea(self.root, bg=BG)
        self.area.pack(fill="both", expand=True)
        frame = ttk.Frame(self.area.body)
        frame.pack(fill="both", expand=True, pady=10)
        # a wide screen gets two columns: the controls, and History + the
        # modem log using the window's full height. Narrow: one column.
        outer = ttk.Frame(frame)
        logs_col = ttk.Frame(frame)
        self.wide = self.root.winfo_screenwidth() >= 1150
        if self.wide:
            outer.grid(row=0, column=0, sticky="nsw")
            logs_col.grid(row=0, column=1, sticky="nsew", padx=(0, 4))
            frame.columnconfigure(1, weight=1)
            frame.rowconfigure(0, weight=1)
        else:
            outer.grid(row=0, column=0, sticky="nsew")
            logs_col.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)

        ttk.Label(outer, text="HF CONTACT", style="Title.TLabel").pack(anchor="w", **pad)
        ttk.Label(outer, text="Echo test and short messages over HF, through your own Mercury modem.",
                  style="Dim.TLabel", wraplength=700, justify="left").pack(anchor="w", **pad)
        self.update_btn = ttk.Button(outer, text="", style="Update.TButton", command=self._update_now)
        self.update_ver = None

        # the station we call
        card = ttk.Frame(outer, style="Card.TFrame", padding=10)
        card.pack(fill="x", pady=(10, 6), **pad)
        self.update_anchor = card
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Calling", style="CardDim.TLabel").pack(side="left")
        self.station_lbl = ttk.Label(top, text="", style="Call.TLabel")
        self.station_lbl.pack(side="left", padx=8)
        ttk.Button(top, text="Settings…", style="Link.TButton", command=self._settings).pack(side="right")
        self.station_info = ttk.Label(card, text="", style="CardDim.TLabel", wraplength=690, justify="left")
        self.station_info.pack(anchor="w", pady=(4, 0))
        live = ttk.Frame(card, style="Card.TFrame")
        live.pack(fill="x", pady=(6, 0))
        self.live_light = tk.Canvas(live, width=12, height=12, bg=PANEL, highlightthickness=0)
        self.live_light.pack(side="left")
        self.live_dot = self.live_light.create_oval(1, 1, 11, 11, fill=GRAY, outline="")
        self.live_lbl = ttk.Label(live, text="Checking whether the station is on the air…", style="CardDim.TLabel",
                                  wraplength=660, justify="left")
        self.live_lbl.pack(side="left", padx=6)
        self.live_state = "unknown"
        ver = ttk.Frame(card, style="Card.TFrame")
        ver.pack(fill="x", pady=(4, 0))
        self.ver_lbl = ttk.Label(ver, text="", style="CardDim.TLabel", wraplength=660, justify="left")
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
        self.msg = tk.Text(outer, height=4, wrap="word", bg=FIELD, fg=FG, insertbackground=FG,
                           relief="flat", highlightthickness=1, highlightbackground=BORDER,
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
                              "and anyone can read it.", style="Dim.TLabel", wraplength=700,
                  justify="left").pack(anchor="w", pady=(2, 0), **pad)

        # your own echo service
        svc = ttk.Frame(outer, style="Card.TFrame", padding=(10, 6))
        svc.pack(fill="x", pady=(10, 0), **pad)
        self.svc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(svc, text="Run an echo service", variable=self.svc_var, style="Card.TCheckbutton",
                        command=self._svc_toggle).pack(anchor="w")
        self.svc_lbl = ttk.Label(svc, text="", style="CardDim.TLabel", wraplength=690, justify="left")
        self.svc_lbl.pack(anchor="w", pady=(2, 0))
        self.svc_warn = ttk.Label(svc, text="", style="CardDim.TLabel", wraplength=690, justify="left")
        self._svc_show("off")

        # status
        st = ttk.Frame(outer, style="Card.TFrame", padding=(10, 6))
        st.pack(fill="x", pady=(10, 6), **pad)
        self.light = tk.Canvas(st, width=14, height=14, bg=PANEL, highlightthickness=0)
        self.light.pack(side="left")
        self.dot = self.light.create_oval(2, 2, 12, 12, fill=GRAY, outline="")
        self.status_lbl = ttk.Label(st, text="Ready", style="Card.TLabel", wraplength=680, justify="left")
        self.status_lbl.pack(side="left", padx=8, fill="x", expand=True)
        self.signal_lbl = ttk.Label(outer, text="", style="Dim.TLabel")
        self.signal_lbl.pack(anchor="e", padx=14, pady=(0, 4))

        # history
        outer = logs_col
        hist_head = ttk.Frame(outer)
        hist_head.pack(fill="x", **pad)
        ttk.Label(hist_head, text="History", style="Dim.TLabel").pack(side="left")
        self.log_btn = ttk.Button(hist_head, text="Modem log", style="Link.TButton", command=self._toggle_log)
        self.log_btn.pack(side="right")
        ttk.Button(hist_head, text="Export ADIF…", style="Link.TButton", command=self._export_adif).pack(side="right")
        # History and the modem log share whatever height is left, with a
        # handle between them; they shrink to fit instead of pushing the
        # window off the bottom of the screen
        self.logs = ttk.Panedwindow(outer, orient="vertical")
        self.logs.pack(fill="both", expand=True, pady=(2, 0), **pad)
        self.hist = tk.Text(self.logs, height=6, width=48, wrap="word", bg=FIELD, fg=FG, relief="flat",
                            highlightthickness=0, padx=8, pady=6, state="disabled", font=("TkDefaultFont", 10))
        self.logs.add(self.hist, weight=3)
        for tag, color in (("when", DIM), ("echo", TEAL), ("reply", GREEN), ("sent", ORANGE), ("err", RED),
                           ("service", TEAL)):
            self.hist.tag_configure(tag, foreground=color)
        self.modem_log = tk.Text(self.logs, height=5, width=48, wrap="none", bg=FIELD, fg=DIM, relief="flat",
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

    def _add_history(self, kind, text, save=True, qso=None):
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        if save:
            entry = {"when": when, "kind": kind, "text": text}
            if qso:
                entry["qso"] = qso          # for the ADIF export
            self.store.history.append(entry)
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
        call = self.store.settings["station"].get("call", "")
        self.station_mercury = mv.station_version(doc) if station_status.belongs_to(doc, call) else None
        if state not in ("off", "busy"):
            self.asked -= {("off", call), ("busy", call)}     # back on: ask again next time it goes off
        self._show_version()

    def _show_version(self):
        call = self.store.settings["station"].get("call", "The station")
        res = mv.compare(self.local_mercury, self.station_mercury, call)
        if res is None:
            # no live version for this station: say what we know, check nothing
            res = ("info", mv.reminder(self.local_mercury, call))
        self.ver_kind = res[0]
        if self.ver_kind != "mismatch":
            self.asked = {k for k in self.asked if k[0] != "mismatch"}
        self.ver_lbl.configure(text=res[1] if res else "",
                               foreground={"match": GREEN, "mismatch": ORANGE}.get(self.ver_kind, DIM))
        if self.ver_kind == "mismatch":
            self.ver_link.pack(anchor="w", padx=(12, 0))
        else:
            self.ver_link.pack_forget()

    def _go_anyway(self):
        """Ask before calling a station that says it's off the air, busy, or on
        another Mercury version -- once. After a yes, the line under "Calling"
        keeps saying it, but there's no pop-up again until things change.
        Unknown (no internet, no status) never stops anyone."""
        call = self.store.settings["station"]["call"]
        if self.live_state == "off":
            key, text = ("off", call), f"{call} looks off the air right now, so nobody may answer."
        elif self.live_state == "busy":
            key, text = ("busy", call), f"{call} is in a call with someone else right now."
        elif self.ver_kind == "mismatch":
            key, text = ("mismatch", call, self.local_mercury, self.station_mercury), self.ver_lbl.cget("text")
        else:
            return True
        if key in self.asked:
            return True
        ok = messagebox.askyesno("HF Contact", text + "\n\nCall anyway? (You won't be asked again about "
                                 "this unless it changes.)")
        if ok:
            self.asked.add(key)
        return ok

    def _toggle_log(self):
        if self.log_shown:
            self.logs.forget(self.modem_log)
        else:
            self.logs.add(self.modem_log, weight=2)
        self.log_shown = not self.log_shown
        self.log_btn.configure(text="Hide modem log" if self.log_shown else "Modem log")

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

    def _start(self, cfg, echo=False, message=None, typed=None, check=True):
        if self.busy:
            return
        if self.svc and self.svc.linked_with:
            messagebox.showinfo("HF Contact", f"{self.svc.linked_with} is linked to your echo service right "
                                f"now. Try again when they're done (a minute or two at most).")
            return
        if check and not self._go_anyway():
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
        self._pending_cfg = cfg

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
                elif kind == "stations":
                    if val is not None:
                        self.stations = val["stations"]
                        self.list_submit = val.get("submit")
                        self.list_cached_at = val.get("cached_at")
                elif kind == "update":
                    self._update_found(val)
                elif kind == "update-asked":
                    self._update_found(val, asked=True)
                elif kind == "updated":
                    self._updated(*val)
                elif kind == "rig":
                    if val != self.rig_mhz:
                        self.rig_mhz = val
                        self._svc_show(self.svc_state)
                elif kind.startswith("svc-"):
                    self._svc_event(kind, val)
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _done(self, r):
        self.busy = False
        list_request, self._list_request_pending = self._list_request_pending, False
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

        contact = [self._session_qso(r)] if r.linked else [None]

        def add(kind, text):                    # the call's contact goes on its first History entry
            self._add_history(kind, text, qso=contact[0])
            contact[0] = None
        if r.echo_reply:
            add("echo", r.echo_reply)
        if r.message_sent and r.linked and not r.error:
            add("sent", self._pending_typed or "(message)")
            self.msg.delete("1.0", "end")
        for name, body in r.replies:
            add("reply", body)
        if r.error:
            if list_request and not r.linked:
                r.error += (" Your station list request didn't go out. Try Email or Copy instead "
                            "(Settings \u2192 List my station).")
            add("err", r.error)
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
        ok, text = es.segment_check(self._svc_dial())
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
        if self.svc_var.get():
            self._read_rig()
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
            text = f"Answered an echo test from {val['call']}: heard them {snr}, {bps}."
            s = self.store.settings
            self._add_history("service", text, qso=adif.qso(
                val["call"], s["mycall"], mhz=self._svc_dial(),
                grid=self.grid_var.get().strip().upper() or s.get("grid", ""),
                comment=f"answered their echo test, heard them {snr}, {bps}"))
            if s.get("echo_notify", True):
                notify.notify("HF Contact: echo test answered",
                              f"{val['call']} tested against your station. You heard them {snr}, {bps}.",
                              icon=str(resource("icon.png")))
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

    def _svc_dial(self):
        """The dial frequency for the segment check: the radio's own reading
        (rigctld) when there is one, otherwise what Settings says."""
        if self.rig_mhz is not None:
            return self.rig_mhz
        return es.parse_mhz(self.store.settings.get("echo_dial", ""))

    def _read_rig(self):
        addr = self.store.settings.get("rig_addr", "")
        if not addr:
            self.events.put(("rig", None))
            return
        threading.Thread(target=lambda: self.events.put(("rig", rig.read_mhz(addr))), daemon=True).start()

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
            ok, warn = es.segment_check(self._svc_dial())
            if ok is None:
                warn, wcolor = ("Set your dial frequency in Settings to check it against the US "
                                "automatic-control segments.", DIM)
            else:
                wcolor = GREEN if ok else ORANGE
                if self.rig_mhz is not None:
                    warn = f"Radio reads {self.rig_mhz:.4f} MHz. " + warn
            self.svc_warn.configure(text=warn, foreground=wcolor)
            self.svc_warn.pack(anchor="w", pady=(2, 0))
        else:
            self.svc_warn.pack_forget()

    # ------------------------------------------------------------ settings

    def _session_qso(self, r):
        cfg = self._pending_cfg
        notes = []
        if r.echo_reply:
            notes.append(f"echo test, they heard me {echo_summary(r.echo_reply) or 'fine'}")
        if r.heard_snr is not None:
            notes.append(f"I heard them SNR {r.heard_snr:+.1f} dB")
        if r.message_sent and not r.error:
            notes.append("message sent")
        if r.replies:
            notes.append(f"{len(r.replies)} repl{'y' if len(r.replies) == 1 else 'ies'} received")
        mhz = es.parse_mhz(self.store.settings["station"].get("freq", ""))
        return adif.qso(cfg["target"], cfg["mycall"], mhz=mhz, grid=cfg.get("grid", ""), comment="; ".join(notes))

    def _export_adif(self):
        text, n = adif.export(self.store.history)
        if not n:
            messagebox.showinfo("Export ADIF", "No contacts in History to export yet. (History from before "
                                "version 0.5.0 doesn't record them.)")
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Export ADIF",
                                            defaultextension=".adi", initialfile="hf-contact.adi",
                                            filetypes=[("ADIF", "*.adi"), ("All files", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            messagebox.showwarning("Export ADIF", f"Couldn't save it: {e}")
            return
        messagebox.showinfo("Export ADIF", f"Saved {n} contact{'' if n == 1 else 's'} to {path}.\n\n"
                            f"Logged as mode DYNAMIC (ADIF has no Mercury mode yet), with Mercury in the comment.")

    def _fetch_stations(self):
        url = self.store.settings.get("stations_url", "")
        cache = self.store.dir / "stations-cache.json"
        threading.Thread(target=lambda: self.events.put(("stations", station_list.fetch_cached(url, cache))),
                         daemon=True).start()

    def _update_found(self, version, asked=False):
        if version:
            self.update_ver = version
            self.update_btn.configure(text=f"HF Contact {version} is out -- Update now \u2192")
            self.update_btn.pack(anchor="w", padx=14, before=self.update_anchor)
            if asked:
                self._update_now()
        elif asked:
            messagebox.showinfo("HF Contact", f"You have the latest: HF Contact {__version__}.", parent=self.root)

    def check_update_now(self):
        """Settings > Check for updates: ask GitHub right away."""
        threading.Thread(target=lambda: self.events.put(("update-asked", updates.check(__version__))),
                         daemon=True).start()

    def _update_now(self):
        v = self.update_ver
        if not v:
            return
        if self.busy or self.svc:
            messagebox.showinfo("HF Contact", "Finish the call (and switch the echo service off) first, then "
                                "Update now.", parent=self.root)
            return
        mode = updates.how()
        where = {"git": "This folder is a git clone: it moves forward to the new version.",
                 "zip": "The program files are replaced; the old ones are kept in hf_contact.old.",
                 "exe": "The new HF-Contact.exe replaces this one; the old one is kept as .old."}[mode]
        if not messagebox.askyesno("Update HF Contact", f"Update from {__version__} to {v}?\n\n{where} Your "
                                   "settings and history stay. HF Contact starts again afterwards.\n\n"
                                   "What's new: github.com/Saltyhaus/hf-contact/releases", parent=self.root):
            return
        self.update_btn.configure(text=f"Updating to {v}\u2026", state="disabled")
        threading.Thread(target=lambda: self.events.put(("updated", (v, *updates.install(v)))), daemon=True).start()

    def _updated(self, version, ok, msg):
        self.update_btn.configure(state="normal")
        if not ok:
            self.update_btn.configure(text=f"HF Contact {version} is out -- Update now \u2192")
            if messagebox.askyesno("Not updated", f"{msg}\n\nOpen the download page instead?", parent=self.root):
                webbrowser.open(updates.RELEASES_PAGE)
            return
        messagebox.showinfo("HF Contact", f"{msg[0].upper() + msg[1:]}. Starting it again now.", parent=self.root)
        self._restart()

    def _restart(self):
        """Close properly, then start HF Contact again the same way."""
        import subprocess
        cmd = updates.restart_cmd()
        if not self._quit(ask=False):
            return
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen(cmd, cwd=str(Path(sys.executable).parent))
            else:
                os.chdir(str(updates.app_root()))
                os.execv(cmd[0], cmd)
        except OSError as e:
            print(f"couldn't start HF Contact again: {e}", file=sys.stderr)

    def _check_update(self):
        """Once a day: is there a newer release? Quiet line if so."""
        s = self.store.settings
        now = time.time()
        if now - float(s.get("update_checked") or 0) >= updates.DAY:
            s["update_checked"] = now
            self.store.save()
            threading.Thread(target=lambda: self.events.put(("update", updates.check(__version__))),
                             daemon=True).start()
        self.root.after(6 * 3600 * 1000, self._check_update)

    # ------------------------------------------------------------ list my station

    def _list_my_station(self, parent=None):
        s = self.store.settings
        sub = self.list_submit or {}
        win = tk.Toplevel(parent or self.root, bg=BG)
        win.title("List my station")
        win.transient(parent or self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        owner = sub.get("call") or "the list's owner"
        ttk.Label(frm, text=f"Ask {owner} to add your station to the station list (or take it off). "
                            f"It's a request: they check it and add it by hand.", style="Dim.TLabel",
                  wraplength=420, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        action = tk.StringVar(value="add")
        r = ttk.Frame(frm)
        r.grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(r, text="Add or update my station", value="add", variable=action).pack(side="left")
        ttk.Radiobutton(r, text="Remove it", value="remove", variable=action).pack(side="left", padx=12)
        call_v = tk.StringVar(value=self.call_var.get().strip().upper() or s.get("mycall", ""))
        grid_v = tk.StringVar(value=(self.grid_var.get().strip().upper() or s.get("grid", ""))[:4])
        dial = self._svc_dial()
        freq_v = tk.StringVar(value=f"{dial:.3f}" if dial else "")
        hrs = [f"{h:02d}:00" for h in range(24)]
        a = int(s.get("echo_from", 18)) % 24 if s.get("echo_schedule") == "hours" else 0
        b = int(s.get("echo_to", 22)) % 24 if s.get("echo_schedule") == "hours" else 0
        from_v, to_v = tk.StringVar(value=hrs[a]), tk.StringVar(value=hrs[b])
        echo_v = tk.BooleanVar(value=bool(self.svc_var.get()))
        note_v = tk.StringVar()
        public_v = tk.BooleanVar(value=False)
        rows = (("Your callsign", ttk.Entry(frm, textvariable=call_v, width=14)),
                ("Grid (4 characters)", ttk.Entry(frm, textvariable=grid_v, width=8)),
                ("Frequency, MHz", ttk.Entry(frm, textvariable=freq_v, width=10)))
        for i, (label, w) in enumerate(rows, start=2):
            ttk.Label(frm, text=label, style="Dim.TLabel").grid(row=i, column=0, sticky="w", pady=3, padx=(0, 10))
            w.grid(row=i, column=1, sticky="w", pady=3)
        hr = ttk.Frame(frm)
        hr.grid(row=5, column=1, sticky="w", pady=3)
        ttk.Label(frm, text="Usually listening", style="Dim.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 10))
        ttk.Combobox(hr, textvariable=from_v, values=hrs, width=6, state="readonly").pack(side="left")
        ttk.Label(hr, text="to").pack(side="left", padx=4)
        ttk.Combobox(hr, textvariable=to_v, values=hrs, width=6, state="readonly").pack(side="left")
        utc_lbl = ttk.Label(hr, text="", style="Dim.TLabel")
        utc_lbl.pack(side="left", padx=6)

        def show_utc(*_):
            fa, fb = int(from_v.get()[:2]), int(to_v.get()[:2])
            if fa == fb:
                utc_lbl.configure(text="(any time)")
            else:
                ua, ub = listing.utc_hours(fa, fb)
                utc_lbl.configure(text=f"your time = {ua:02d}–{ub:02d} UTC")
        from_v.trace_add("write", show_utc)
        to_v.trace_add("write", show_utc)
        show_utc()
        ttk.Checkbutton(frm, text="I run an HF Contact echo service", variable=echo_v).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(frm, text="Note (optional)", style="Dim.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(frm, textvariable=note_v, width=34).grid(row=7, column=1, sticky="w", pady=3)
        ttk.Checkbutton(frm, text="OK to show this publicly on the station list", variable=public_v).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(6, 3))
        mv_state, mv_ver = self.local_mercury
        info = ttk.Label(frm, text=(f"Your Mercury version ({mv_ver}) is added automatically." if mv_ver else
                                    "Start Mercury first and its version is added too."),
                         style="Dim.TLabel", wraplength=420, justify="left")
        info.grid(row=9, column=0, columnspan=2, sticky="w", pady=(2, 8))

        def request():
            fa, fb = int(from_v.get()[:2]), int(to_v.get()[:2])
            text, problems = listing.build(
                action.get(), call_v.get(), grid_v.get(), es.parse_mhz(freq_v.get()),
                None if fa == fb else listing.utc_hours(fa, fb), echo_v.get(), mv_ver or "", note_v.get(),
                public_v.get() or action.get() == "remove")
            if problems:
                messagebox.showwarning("List my station", "\n".join(problems), parent=win)
            return text

        def by_mercury():
            text = request()
            if not text:
                return
            mycall = call_v.get().strip().upper()
            cfg = {"mycall": mycall, "target": sub["call"], "host": s.get("host", "127.0.0.1"),
                   "port": int(s.get("port", 8300)), "grid": grid_v.get().strip().upper()}
            win.destroy()
            self._list_request_pending = True
            self._start(cfg, message=text, typed=text, check=False)

        def by_email():
            text = request()
            if text:
                webbrowser.open(listing.mailto(sub["email"], call_v.get().strip().upper(), text))

        def copy():
            text = request()
            if text:
                win.clipboard_clear()
                win.clipboard_append((f"To: {sub['email']}\n\n" if sub.get("email") else "") + text)
                messagebox.showinfo("List my station", "Copied. Paste it into an email"
                                    + (f" to {sub['email']}." if sub.get("email") else "."), parent=win)

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=(0, 8))
        cp = ttk.Button(btns, text="Copy", command=copy)
        cp.pack(side="left", padx=(0, 8))
        em = ttk.Button(btns, text="Email", command=by_email)
        em.pack(side="left", padx=(0, 8))
        hf = ttk.Button(btns, text=f"Send over Mercury", style="Go.TButton", command=by_mercury)
        hf.pack(side="left")
        if not sub.get("email"):
            em.state(["disabled"])
        if not sub.get("call") or self.busy:
            hf.state(["disabled"])
        if not sub:
            ttk.Label(frm, text="The station list hasn't loaded (no internet, or no list URL in Settings), so "
                                "there's nowhere to send this yet.", style="Dim.TLabel", foreground=ORANGE,
                      wraplength=420, justify="left").grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _settings(self):
        s = self.store.settings
        win = tk.Toplevel(self.root, bg=BG)
        win.title("Settings")
        win.transient(self.root)
        area = scroll.ScrollArea(win, bg=BG)
        area.pack(fill="both", expand=True)
        frm = ttk.Frame(area.body, padding=14)
        frm.pack(fill="both", expand=True)
        fields = [("Station to call", "call", s["station"].get("call", "")),
                  ("Its frequency (shown only)", "freq", s["station"].get("freq", "")),
                  ("Note (shown only)", "note", s["station"].get("note", "")),
                  ("Mercury address", "host", s.get("host", "127.0.0.1")),
                  ("Mercury TNC port", "port", str(s.get("port", 8300))),
                  ("Live status URL (optional)", "status_url", s.get("status_url", "")),
                  ("Station list URL (optional)", "stations_url", s.get("stations_url", ""))]
        vars_ = {}
        picks = {station_list.label(st): st for st in self.stations}
        if picks and self.list_cached_at:
            ttk.Label(frm, text=f"Station list: saved copy from {self.list_cached_at[:10]} (no internet now)",
                      style="Dim.TLabel").grid(row=len(fields) + 4, column=0, columnspan=2, sticky="w")
        for i, (label, key, val) in enumerate(fields):
            ttk.Label(frm, text=label, style="Dim.TLabel").grid(row=i, column=0, sticky="w", pady=4, padx=(0, 10))
            v = tk.StringVar(value=val)
            if key == "call" and picks:
                # pick from the published list, or type any call
                box = ttk.Combobox(frm, textvariable=v, values=list(picks), width=30)
                box.grid(row=i, column=1, sticky="we", pady=4)

                def picked(_e=None, v=v):
                    st = picks.get(v.get())
                    if st:
                        for k, val2 in station_list.as_settings(st).items():
                            vars_[k].set(val2)          # including an empty status URL
                box.bind("<<ComboboxSelected>>", picked)
            else:
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
        row4 = ttk.Frame(eb)
        row4.pack(anchor="w", fill="x", pady=(6, 0))
        ttk.Label(row4, text="Or read it from rigctld (host:port)", style="Dim.TLabel").pack(side="left")
        rig_v = tk.StringVar(value=s.get("rig_addr", ""))
        ttk.Entry(row4, textvariable=rig_v, width=16).pack(side="left", padx=8)
        check = ttk.Label(eb, text="", style="Dim.TLabel", wraplength=420, justify="left")
        check.pack(anchor="w", pady=(4, 0))
        notify_v = tk.BooleanVar(value=s.get("echo_notify", True))
        ttk.Checkbutton(eb, text="Pop up a notice when someone tests against me", variable=notify_v).pack(
            anchor="w", pady=(6, 0))
        ttk.Label(eb, text=es.SEGMENT_NOTE + " Your radio isn't tuned by HF Contact: this is only checked.",
                  style="Dim.TLabel", wraplength=420, justify="left").pack(anchor="w", pady=(4, 0))

        def check_dial(*_):
            ok, text = es.segment_check(es.parse_mhz(dial_v.get()))
            if ok is None and dial_v.get().strip():
                text, ok = "Not a frequency I can read (try 14.105).", False
            elif self.rig_mhz is not None and rig_v.get().strip():
                ok, text = es.segment_check(self.rig_mhz)
                text = f"Radio reads {self.rig_mhz:.4f} MHz. " + text
            check.configure(text=text, foreground={True: GREEN, False: ORANGE}.get(ok, DIM))
        dial_v.trace_add("write", check_dial)
        rig_v.trace_add("write", check_dial)
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
            s["rig_addr"] = rig_v.get().strip()
            s["echo_notify"] = bool(notify_v.get())
            s["stations_url"] = vars_["stations_url"].get().strip()
            size = size_v.get().lower()
            size_changed = size != str(s.get("text_size", "normal")).lower()
            s["text_size"] = size
            theme = themes.by_label(look_v.get())
            size_changed = size_changed or theme != s.get("theme", themes.DEFAULT)
            s["theme"] = theme
            self.store.save()
            self.rig_mhz = None
            self._fetch_stations()
            self._svc_show(self.svc_state)
            self._svc_tick(reschedule=False)
            self._show_station()
            win.destroy()
            if size_changed:
                if self.busy or self.svc:
                    messagebox.showinfo("HF Contact", "The new look and text size apply the next time you open "
                                        "HF Contact.", parent=self.root)
                elif messagebox.askyesno("HF Contact", "Restart HF Contact now to use the new look / text size?",
                                         parent=self.root):
                    self.root.after(100, self._restart)
                    return
            self._show_live(None)
            threading.Thread(target=lambda: self.events.put(("live", station_status.fetch(s["status_url"]))),
                             daemon=True).start()

        def reset():
            for k, v in DEFAULT_STATION.items():
                vars_[k].set(v)
            vars_["host"].set("127.0.0.1")
            vars_["port"].set("8300")
            vars_["status_url"].set(station_status.DEFAULT_URL)
            vars_["stations_url"].set(station_list.DEFAULT_URL)

        # Save and Cancel stay on screen; only the fields above them scroll
        bar = ttk.Frame(win, padding=(14, 8))
        bar.pack(side="bottom", fill="x", before=area)
        btns = ttk.Frame(bar)
        btns.pack(side="top", anchor="e")
        ttk.Button(btns, text="List my station…", style="Update.TButton",
                   command=lambda: self._list_my_station(win)).pack(side="left", padx=(0, 24))
        ttk.Button(btns, text="Defaults", command=reset).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Save", style="Go.TButton", command=save).pack(side="left")
        foot = ttk.Frame(bar)
        foot.pack(side="top", fill="x", pady=(8, 0))
        ttk.Label(foot, text=f"HF Contact {__version__}", style="Dim.TLabel").pack(side="left")
        ttk.Button(foot, text="Check for updates", style="Update.TButton",
                   command=self.check_update_now).pack(side="left", padx=(10, 0))
        size_v = tk.StringVar(value=str(s.get("text_size", "normal")).capitalize())
        ttk.Combobox(foot, textvariable=size_v, values=["Small", "Normal", "Large"], width=8,
                     state="readonly").pack(side="right")
        ttk.Label(foot, text="Text size", style="Dim.TLabel").pack(side="right", padx=(0, 6))
        look_v = tk.StringVar(value=themes.get(s.get("theme")).get("label"))
        ttk.Combobox(foot, textvariable=look_v, values=[lb for _k, lb in themes.CHOICES], width=13,
                     state="readonly").pack(side="right", padx=(0, 14))
        ttk.Label(foot, text="Look", style="Dim.TLabel").pack(side="right", padx=(0, 6))
        frm.columnconfigure(1, weight=1)
        win.update_idletasks()
        scroll.fit_to_screen(win, area.body.winfo_reqwidth(), area.needed_height())

    def _quit(self, ask=True):
        if ask and self.busy and not messagebox.askyesno("HF Contact", "A call is in progress. Hang up and quit?"):
            return False
        if self.cancel:
            self.cancel.set()
        svc = self._svc_stop("off")
        if svc:
            svc.join(12)                     # hang up on a caller properly before we go
            if not svc.running:
                self._svc_mark(False)
        try:
            self.store.settings["window"] = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
            self.store.save()
        except tk.TclError:
            pass
        self.root.destroy()
        return True


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
    settings = Store().settings
    apply_theme(settings.get("theme"))
    try:
        size = scroll.text_scale(settings.get("text_size"))
        root.tk.call("tk", "scaling", max(1.0, root.winfo_fpixels("1i") / 72.0) * size)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
