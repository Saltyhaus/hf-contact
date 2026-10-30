"""
Which Mercury is running on this computer, and does it match the station's?

Different Mercury versions can hear each other's calls without ever
connecting (1.9.13 and 1.9.21 don't), so before you call, the app compares
your Mercury with the version the station publishes in its live status
(hf-contact.json, "mercury_version").

Mercury's TNC port can't tell us: it answers VERSION with a fixed
"VARA version 4.9.0" so Winlink apps accept it. So we ask the Mercury
program itself, `mercury -V` (1.9.21 and newer). An older build doesn't
know -V and prints its usage instead, which tells us it's older.

Never blocks anything: no internet, no Mercury running, or anything
unexpected just means no check.
"""
import os
import re
import subprocess
import sys

VERSION_RE = re.compile(r"Mercury Version (\d{1,3}\.\d{1,3}\.\d{1,3})")
PLAIN_RE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}")
RELEASES_URL = "https://github.com/Rhizomatica/mercury/releases"
NAMES = ("mercury", "mercury-ui")            # the modem, and the GUI that has the modem built in
_NO_WINDOW = 0x08000000                       # Windows: don't flash a console


def _run(cmd, timeout=6):
    kw = {"creationflags": _NO_WINDOW} if sys.platform == "win32" else {}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, **kw)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return (r.stdout or "") + (r.stderr or "")


def running_mercury(proc="/proc"):
    """Paths of the Mercury programs running now (modem or GUI)."""
    if sys.platform == "win32":
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-Process -Name mercury,mercury-ui -ErrorAction SilentlyContinue | "
                    "ForEach-Object { $_.Path }"], timeout=15)
        return [l.strip() for l in (out or "").splitlines() if l.strip().lower().endswith(".exe")]
    found = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "comm")) as f:
                if f.read().strip() not in NAMES:
                    continue
            found.append(os.readlink(os.path.join(proc, pid, "exe")).replace(" (deleted)", ""))
        except OSError:
            continue
    return found


def _modem_binary(path):
    """The GUI can't answer -V; the modem next to it (same install) can."""
    folder, name = os.path.split(path)
    if name.lower().startswith("mercury-ui"):
        sibling = os.path.join(folder, "mercury.exe" if name.lower().endswith(".exe") else "mercury")
        return sibling if os.path.isfile(sibling) else None
    return path


def ask_version(binary):
    """("ok", "1.9.21"), ("old", None) if it ran but doesn't know -V, or (None, None)."""
    out = _run([binary, "-V"])
    if out is None:
        return None, None
    m = VERSION_RE.search(out)
    return ("ok", m.group(1)) if m else ("old", None)


def local_version(proc="/proc"):
    """("ok", version) / ("old", None) / ("not-running", None) / ("unknown", None)."""
    paths = running_mercury(proc)
    if not paths:
        return "not-running", None
    result = ("unknown", None)
    for p in paths:
        binary = _modem_binary(p)
        if not binary:
            continue
        state, version = ask_version(binary)
        if state == "ok":
            return state, version
        if state == "old":
            result = ("old", None)
    return result


def station_version(doc):
    v = doc.get("mercury_version") if isinstance(doc, dict) else None
    return v if isinstance(v, str) and PLAIN_RE.fullmatch(v) else None


def compare(local, station, call="The station"):
    """(kind, text) for the app, or None when there's nothing to say.
    kind: match / mismatch / info. local is a local_version() result."""
    if not station:
        return None                                     # no internet, or the station doesn't publish it
    state, mine = local
    if state == "ok" and mine == station:
        return "match", f"Mercury {mine} ✓ same as {call}"
    if state == "ok":
        return "mismatch", (f"You have Mercury {mine}; {call} runs {station}. Different versions hear each "
                            f"other but don't connect, so install {station} to call {call}.")
    if state == "old":
        return "mismatch", (f"Your Mercury is older than {station} (it doesn't report a version); {call} runs "
                            f"{station}. Different versions hear each other but don't connect.")
    if state == "not-running":
        return "info", f"{call} runs Mercury {station}. Start Mercury and yours is checked too."
    return "info", f"{call} runs Mercury {station}; make sure yours is the same."
