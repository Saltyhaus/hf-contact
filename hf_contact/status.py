"""
The station's live status (the same JSON its website shows), so the app
can say whether anyone's listening before you key up. Optional: no
internet (common in the field) just means "unknown", never a block.
"""
import json
import time
import urllib.request

DEFAULT_URL = "https://ag7jc.com/station.json"


def fetch(url, timeout=6):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hf-contact"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            doc = json.loads(r.read(65536).decode("utf-8"))
            return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _ago(sec):
    if sec < 90:
        return "just now"
    if sec < 3600:
        return f"{round(sec / 60)} min ago"
    if sec < 172800:
        return f"{round(sec / 3600)} h ago"
    return f"{round(sec / 86400)} days ago"


def describe(doc, now=None):
    """(state, text): state is listening / busy / off / unknown."""
    if not doc:
        return "unknown", "Live status unavailable (no internet?) -- you can still call"
    now = now or time.time()
    call = str(doc.get("callsign", "The station"))[:12]
    try:
        age = now - int(doc.get("updated_unix", 0))
        stale = age > int(doc.get("stale_after", 300))
    except (TypeError, ValueError):
        return "unknown", "Live status unreadable -- you can still call"
    status = "off" if stale else doc.get("status")
    freq = doc.get("freq_hz")
    on = f" on {freq / 1e6:.3f} MHz".replace("0 MHz", " MHz") if isinstance(freq, int) else ""
    if status == "listening":
        return "listening", f"{call} is listening now{on}"
    if status == "busy":
        return "busy", f"{call} is in a call right now -- try again in a few minutes"
    return "off", f"{call} is off the air (last update {_ago(max(0, age))})"
