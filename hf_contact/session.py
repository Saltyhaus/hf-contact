"""
One radio session with the station: call it, send an echo ping and/or a
message, collect whatever comes back (the echo reply, replies waiting for
us), hang up.

The other end runs mercury-connector with guest mode (-g):
  - a frame named "echo..." gets an "echo-reply.txt" back in the same
    session, saying how that station heard us
  - any other frame is a short text message (4 KB max, 5 per session)
  - replies the operator wrote for us are sent as soon as we connect
"""

import queue
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .tnc import MercuryTnc, TncError

CALL_RE = re.compile(r"^(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{3,10}(-[0-9]{1,2})?$")
MAX_MESSAGE = 2000          # the station accepts 4 KB; leave room for the signature


def valid_call(call):
    return bool(CALL_RE.match((call or "").strip().upper()))


@dataclass
class Result:
    linked: bool = False
    error: str = ""
    heard_snr: float = None          # how WE heard the station
    bitrate: int = None
    echo_sent: bool = False
    echo_reply: str = ""             # the station's report on us
    message_sent: bool = False
    replies: list = field(default_factory=list)   # [(name, text)]
    log: list = field(default_factory=list)


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def compose_message(text, name="", grid=""):
    sig = ", ".join(p for p in (name.strip(), grid.strip().upper()) if p)
    text = text.strip()
    return f"{text}\n\n-- {sig}" if sig else text


def reply_body(raw):
    """Replies come in the station's spool format (headers, blank line,
    body); show just the body."""
    text = raw.decode("utf-8", "replace").replace("\r", "")
    head, sep, body = text.partition("\n\n")
    if sep and all(":" in l for l in head.splitlines() if l.strip()):
        return body.strip()
    return text.strip()


def echo_summary(reply_text):
    """'SNR +6.2 dB, speed 1400 bps' out of the echo reply, if present."""
    m = re.search(r"Your signal here:\s*(.+)", reply_text)
    return m.group(1).strip() if m else ""


def run_session(cfg, echo=False, message=None, progress=lambda kind, text: None,
                connect_timeout=180, idle_after=25, echo_timeout=180, max_seconds=900, cancel=None):
    """cfg: dict with mycall, target, host, port, grid (optional).
    Blocking -- run it in a worker thread. progress(kind, text) is called
    from this thread for status updates."""
    r = Result()
    mycall = cfg["mycall"].strip().upper()
    target = cfg["target"].strip().upper()

    def say(kind, text):
        r.log.append(text)
        progress(kind, text)

    tnc = MercuryTnc(cfg.get("host", "127.0.0.1"), cfg.get("port", 8300))
    try:
        say("status", "Opening Mercury…")
        tnc.open(mycall)
        if tnc.version:
            say("info", f"Mercury {tnc.version}")
        say("status", f"Calling {target}…")
        tnc.call(mycall, target)

        start = time.time()
        while True:
            if cancel is not None and cancel.is_set():
                r.error = "Cancelled."
                tnc.hang_up()
                return r
            left = connect_timeout - (time.time() - start)
            if left <= 0:
                r.error = (f"No answer from {target} after {connect_timeout} s. Check the frequency and "
                           f"that you're within range, or try another time.")
                tnc.hang_up()
                return r
            try:
                kind, val = tnc.events.get(timeout=min(left, 1))
            except queue.Empty:
                continue
            if kind == "line":
                say("line", val)
            elif kind == "connected":
                break
            elif kind == "disconnected":
                r.error = f"{target} didn't answer (the link was not made)."
                return r
            elif kind == "closed":
                r.error = val
                return r

        r.linked = True
        say("linked", f"Linked with {target}")
        sent_any = False
        if echo:
            ping = f"Echo test from {mycall}" + (f" in {cfg['grid'].upper()}" if cfg.get("grid") else "")
            tnc.send("echo.txt", ping)
            r.echo_sent = sent_any = True
            say("status", "Echo ping sent, waiting for the reply…")
        if message:
            tnc.send(f"msg-{utc_stamp()}.txt", message)
            r.message_sent = sent_any = True
            say("status", "Message sent…")
        if not sent_any:
            say("status", "Waiting for anything the station has for you…")

        sent_at = last = time.time()
        buffer_seen = buffer_drained = False
        while time.time() - start < max_seconds:
            if cancel is not None and cancel.is_set():
                r.error = "Cancelled -- anything not yet sent may not have gone out."
                break
            try:
                kind, val = tnc.events.get(timeout=0.5)
            except queue.Empty:
                kind = None
            now = time.time()
            if kind == "line":
                say("line", val)
            elif kind == "snr":
                r.heard_snr = val
                say("signal", "")
            elif kind == "bitrate":
                r.bitrate = val
                say("signal", "")
            elif kind == "buffer":
                last = now
                if val > 0:
                    buffer_seen = True
                elif buffer_seen:
                    buffer_drained = True
            elif kind == "message":
                last = now
                name, payload = val
                if name.lower().startswith("echo-reply"):
                    r.echo_reply = payload.decode("utf-8", "replace").strip()
                    say("echo", r.echo_reply)
                else:
                    body = reply_body(payload)
                    r.replies.append((name, body))
                    say("reply", body)
            elif kind == "disconnected":
                say("status", f"{target} ended the link.")
                break
            elif kind == "closed":
                r.error = val
                break

            ours_out = not sent_any or buffer_drained or (not buffer_seen and now - sent_at > 20)
            echo_done = not echo or r.echo_reply or now - sent_at > echo_timeout
            if echo and not r.echo_reply and now - sent_at > echo_timeout:
                r.error = "No echo reply came back (the station may not have guest mode on)."
            if ours_out and echo_done and now - last > idle_after:
                break

        if tnc.connected:
            say("status", "Hanging up…")
            tnc.hang_up()
            end = time.time() + 20
            while time.time() < end and tnc.connected:
                try:
                    kind, val = tnc.events.get(timeout=0.5)
                    if kind == "line":
                        say("line", val)
                except queue.Empty:
                    pass
        return r
    except TncError as e:
        r.error = str(e)
        return r
    finally:
        tnc.close()
