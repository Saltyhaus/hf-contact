"""
Run your own echo service: Mercury listens for calls to your callsign
and answers echo pings by itself, the same way a mercury-connector
station in guest mode does, so any HF Contact (or OffGrid Comms) user
can test against you.

Echo only. A caller's messages are never kept: the service drops them
and tells the caller, once per link, that it only answers echoes.

Limits, so an unattended station can't be kept on the air:
  - 3 echo replies per link (more pings are ignored)
  - a link is hung up after 3 minutes, or after 60 s with nothing heard
  - each caller gets at most 6 links an hour

When it's on, your station transmits without you at the radio. In the
US that is automatic control (47 CFR 97.221): General class or higher,
and only inside the automatic-control data segments, or with someone
monitoring. segment_check() tells the app when the dial is outside them;
it's a warning, never a block (other countries have other rules, and
you may be monitoring).
"""

import queue
import threading
import time
from datetime import datetime, timezone

from .session import valid_call
from .tnc import MercuryTnc, TncError

MAX_ECHOES = 3               # per link, same as the connector's guest mode
QUOTE_MAX = 120              # characters of the ping quoted back
LINK_CAP = 180               # seconds
IDLE_AFTER = 60              # seconds with nothing from the caller
LINKS_PER_HOUR = 6           # per caller
NOTICE_NAME = "echo-service-notice.txt"

# 47 CFR 97.221(b): where a station under automatic control may send data
# with more than 500 Hz bandwidth (MHz)
AUTO_SEGMENTS = (
    (3.585, 3.600), (7.100, 7.105), (10.140, 10.150),
    (14.0950, 14.0995), (14.1005, 14.112),
    (18.105, 18.110), (21.090, 21.100), (24.925, 24.930), (28.120, 28.189),
)
# where Mercury's 2.3 kHz signal sits above the USB dial frequency (kHz)
SIGNAL_LOW_KHZ, SIGNAL_HIGH_KHZ = 0.35, 2.65

SEGMENT_NOTE = ("US automatic-control data segments (47 CFR 97.221): 20 m 14.0950–14.0995 and "
                "14.1005–14.112 MHz, 40 m 7.100–7.105 MHz. For Mercury's 2.3 kHz signal, set the "
                "dial (USB) to 14.101–14.109 or 7.100–7.102 MHz. General class or higher.")
OUTSIDE_WARNING = ("Warning: you are running outside the automatic control segment in the US. "
                   "You must monitor, or be in an area that allows it.")


def parse_mhz(text):
    """'14.105', '14105 kHz', '14.105 MHz USB' -> 14.105, or None."""
    t = (text or "").strip().lower().replace(",", ".")
    num = ""
    for ch in t:
        if ch.isdigit() or (ch == "." and "." not in num):
            num += ch
        elif num:
            break
    try:
        v = float(num)
    except ValueError:
        return None
    if "khz" in t or v > 1000:
        v /= 1000.0
    return v if 1.0 <= v <= 60.0 else None


def segment_check(dial_mhz):
    """(True, text) when the whole signal is inside a US automatic-control
    segment, (False, warning) when it isn't, (None, "") when no dial is set."""
    if dial_mhz is None:
        return None, ""
    lo = dial_mhz + SIGNAL_LOW_KHZ / 1000.0
    hi = dial_mhz + SIGNAL_HIGH_KHZ / 1000.0
    for a, b in AUTO_SEGMENTS:
        if lo >= a - 1e-9 and hi <= b + 1e-9:
            return True, (f"Signal {lo:.4f}–{hi:.4f} MHz is inside the US automatic-control "
                          f"segment {a:g}–{b:g} MHz.")
    return False, f"{OUTSIDE_WARNING} (Your signal: {lo:.4f}–{hi:.4f} MHz.)"


def in_schedule(settings, now=None):
    """settings: echo_schedule 'always' or 'hours', echo_from / echo_to
    (whole hours, local time; to is exclusive, may wrap past midnight)."""
    if settings.get("echo_schedule", "always") != "hours":
        return True
    now = now or datetime.now()
    try:
        a, b = int(settings.get("echo_from", 0)) % 24, int(settings.get("echo_to", 0)) % 24
    except (TypeError, ValueError):
        return True
    h = now.hour
    if a == b:
        return True
    return a <= h < b if a < b else (h >= a or h < b)


def describe_schedule(settings):
    if settings.get("echo_schedule", "always") != "hours":
        return "all the time"
    try:
        a, b = int(settings.get("echo_from", 0)) % 24, int(settings.get("echo_to", 0)) % 24
    except (TypeError, ValueError):
        return "all the time"
    return "all the time" if a == b else f"{a:02d}:00–{b:02d}:00"


def _clean_quote(payload):
    """Same cleanup as the connector: one line, no control characters."""
    text = payload.decode("utf-8", "replace").replace("\r", "")
    text = "".join(" " if (c in "\n\t" or ord(c) < 0x20 or ord(c) == 0x7f) else c for c in text)
    return text.strip()[:QUOTE_MAX].strip()


def _snr_text(snr):
    if snr is None:
        return "not reported"
    v = int(snr * 10 + (0.5 if snr >= 0 else -0.5))
    return f"{'-' if v < 0 else '+'}{abs(v) // 10}.{abs(v) % 10} dB"


def echo_reply(caller, mycall, snr, bps, payload, now=None):
    """The reply text, byte for byte the format mercury-connector's guest
    mode sends, so the caller's app reads it the same way."""
    now = now or datetime.now(timezone.utc)
    quote = _clean_quote(payload)
    return (f"ECHO {caller} de {mycall}\n"
            f"Copied you at {now.strftime('%Y-%m-%d %H:%MZ')}\n"
            f"Your signal here: SNR {_snr_text(snr)}, speed {f'{bps} bps' if bps else 'not reported'}\n"
            f"You sent: {quote or '(empty ping)'}\n"
            f"73\n")


def notice_text(mycall):
    return (f"{mycall} is running an unattended echo service. It only answers echo tests: "
            f"your message was not kept. 73\n")


class EchoService:
    """Listens on the local Mercury until stop(). progress(kind, value)
    is called from the service thread:

        ("svc-line", text)        control line, for the modem log
        ("svc-status", text)      what it's doing now
        ("svc-linked", call)      a caller is linked
        ("svc-echo", {"call", "snr", "bps"})   an echo was answered
        ("svc-ignored", call)     a message was dropped (echo only)
        ("svc-refused", call)     a caller over the hourly limit was hung up on
        ("svc-unlinked", call)
        ("svc-error", text)       Mercury went away or wouldn't open
        ("svc-stopped", None)     always last
    """

    def __init__(self, cfg, progress=lambda kind, value: None, max_echoes=MAX_ECHOES, link_cap=LINK_CAP,
                 idle_after=IDLE_AFTER, links_per_hour=LINKS_PER_HOUR):
        self.cfg = cfg
        self.mycall = cfg["mycall"].strip().upper()
        self.progress = progress
        self.max_echoes, self.link_cap, self.idle_after = max_echoes, link_cap, idle_after
        self.links_per_hour = links_per_hour
        self.linked_with = None
        self._stop = threading.Event()
        self._thread = None
        self._links = {}            # caller -> [start times in the last hour]

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------ the loop

    def _run(self):
        try:
            self._serve()
        except TncError as e:
            self.progress("svc-error", str(e))
        finally:
            self.linked_with = None
            self.progress("svc-stopped", None)

    def _allowed(self, caller, now):
        recent = [t for t in self._links.get(caller, []) if now - t < 3600]
        if len(recent) >= self.links_per_hour:
            self._links[caller] = recent
            return False
        self._links[caller] = recent + [now]
        return True

    def _serve(self):
        tnc = MercuryTnc(self.cfg.get("host", "127.0.0.1"), self.cfg.get("port", 8300))
        tnc.open(self.mycall, listen=True)
        link = None
        say = self.progress
        say("svc-status", f"Listening for calls to {self.mycall}")
        try:
            while not self._stop.is_set():
                try:
                    kind, val = tnc.events.get(timeout=0.5)
                except queue.Empty:
                    kind = val = None
                now = time.time()
                if kind == "line":
                    say("svc-line", val)
                elif kind == "connected":
                    caller = tnc.link_source or "?"
                    link = {"call": caller, "start": now, "last": now, "echoes": 0, "snr": None,
                            "bps": None, "noticed": False, "ending": False}
                    if not valid_call(caller) or not self._allowed(caller, now):
                        say("svc-refused", caller)
                        link["ending"] = True
                        tnc.hang_up()
                    else:
                        self.linked_with = caller
                        say("svc-linked", caller)
                elif kind == "snr" and link:
                    link["snr"] = val
                elif kind == "bitrate" and link:
                    link["bps"] = val
                elif kind == "buffer" and link:
                    link["last"] = now
                elif kind == "message" and link and not link["ending"]:
                    link["last"] = now
                    self._frame(tnc, link, *val)
                elif kind == "disconnected":
                    if link and self.linked_with:
                        say("svc-unlinked", link["call"])
                    link = None
                    self.linked_with = None
                    say("svc-status", f"Listening for calls to {self.mycall}")
                elif kind == "closed":
                    raise TncError(val)

                if link and not link["ending"]:
                    if now - link["start"] > self.link_cap or now - link["last"] > self.idle_after:
                        link["ending"] = True
                        say("svc-status", f"Hanging up on {link['call']}")
                        tnc.hang_up()
                elif link and link["ending"] and now - link["last"] > self.idle_after + 30:
                    # the hang-up never got a DISCONNECTED back; don't wait forever
                    link = None
                    self.linked_with = None
        finally:
            if tnc.connected:
                try:
                    tnc.hang_up()
                except TncError:
                    pass
                end = time.time() + 10
                while time.time() < end and tnc.connected:
                    try:
                        tnc.events.get(timeout=0.5)
                    except queue.Empty:
                        pass
            tnc.close()

    def _frame(self, tnc, link, name, payload):
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if base[:4].lower() == "echo":
            if link["echoes"] >= self.max_echoes:
                return
            link["echoes"] += 1
            tnc.send("echo-reply.txt", echo_reply(link["call"], self.mycall, link["snr"], link["bps"], payload))
            self.progress("svc-echo", {"call": link["call"], "snr": link["snr"], "bps": link["bps"]})
            return
        # anything else is a message: never kept
        self.progress("svc-ignored", link["call"])
        if not link["noticed"]:
            link["noticed"] = True
            tnc.send(NOTICE_NAME, notice_text(self.mycall))
