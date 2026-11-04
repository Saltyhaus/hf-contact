"""
"List my station": a request to whoever runs the station list, sent over
Mercury or by email. The owner approves it by hand; a request is not a
listing. One plain format both ways, readable by eye and by a script:

    STATION LIST REQUEST
    action: add
    call: VU2XYZ
    grid: MK82
    freq: 14.105
    hours_utc: 13-16
    echo_service: yes
    mercury: 1.9.21
    public: yes
    note: Bangalore, 100 W and a dipole
"""
import re
import urllib.parse
from datetime import datetime

from .session import valid_call

HEADER = "STATION LIST REQUEST"
GRID4 = re.compile(r"^[A-R]{2}[0-9]{2}$")
NOTE_MAX = 120


def utc_hours(local_from, local_to, offset_hours=None):
    """Local listening hours -> UTC, whole hours."""
    if offset_hours is None:
        off = datetime.now().astimezone().utcoffset()
        offset_hours = round(off.total_seconds() / 3600) if off else 0
    return (int(local_from - offset_hours) % 24, int(local_to - offset_hours) % 24)


def build(action, call, grid="", freq_mhz=None, hours_utc=None, echo_service=False, mercury="",
          note="", public=True):
    """(text, problems). problems is a list of what's wrong; text is None then."""
    problems = []
    call = (call or "").strip().upper()
    if not valid_call(call):
        problems.append("Your callsign isn't set (or isn't a callsign).")
    if action not in ("add", "remove"):
        problems.append("Choose add or remove.")
    grid = (grid or "").strip().upper()[:4]
    if action == "add":
        if grid and not GRID4.match(grid):
            problems.append("The grid should look like DM12.")
        if freq_mhz is not None and not (1.8 <= freq_mhz <= 54):
            problems.append("The frequency should be in MHz, e.g. 14.105.")
        if not public:
            problems.append("Tick the box to say it's OK to show this publicly. Nothing is sent without it.")
    if problems:
        return None, problems
    lines = [HEADER, f"action: {action}", f"call: {call}"]
    if action == "add":
        if grid:
            lines.append(f"grid: {grid}")
        if freq_mhz:
            lines.append(f"freq: {freq_mhz:.3f}")
        if hours_utc:
            lines.append(f"hours_utc: {hours_utc[0]:02d}-{hours_utc[1]:02d}")
        lines.append(f"echo_service: {'yes' if echo_service else 'no'}")
        if mercury:
            lines.append(f"mercury: {mercury}")
        lines.append("public: yes")
    note = " ".join((note or "").split())[:NOTE_MAX]
    if note:
        lines.append(f"note: {note}")
    return "\n".join(lines) + "\n", []


def parse(text):
    """The request's fields as a dict, or None if it isn't one."""
    lines = [l.strip() for l in (text or "").replace("\r", "").splitlines() if l.strip()]
    if not lines or lines[0] != HEADER:
        return None
    out = {}
    for l in lines[1:]:
        k, sep, v = l.partition(":")
        if sep and k.strip() in ("action", "call", "grid", "freq", "hours_utc", "echo_service", "mercury",
                                 "public", "note"):
            out[k.strip()] = v.strip()
    return out if out.get("call") and out.get("action") in ("add", "remove") else None


def mailto(email, call, text):
    subject = f"Station list: {'remove' if 'action: remove' in text else 'add'} {call}"
    return "mailto:" + email + "?" + urllib.parse.urlencode({"subject": subject, "body": text},
                                                            quote_via=urllib.parse.quote)
