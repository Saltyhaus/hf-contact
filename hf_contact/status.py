"""
The station's live status (the same JSON its website shows), so the app
can say whether anyone's listening before you key up. Optional: no
internet (common in the field) just means "unknown", never a block.
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = "https://ag7jc.com/data/hf-contact.json"
STALE_AFTER = 15 * 60          # same as the website: older than this = no recent check-in


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


def _parse_iso(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def describe(doc, now=None, call="The station"):
    """(state, text): state is listening / busy / off / unknown.
    doc is the website's hf-contact.json:
      {"status": "listening"|"off", "busy": bool, "freq_mhz": 14.105, "updated": ISO UTC, ...}"""
    if not doc:
        return "unknown", "Live status unavailable (no internet?) -- you can still call"
    now = now or time.time()
    updated = _parse_iso(doc.get("updated"))
    if updated is None:
        return "unknown", "Live status unreadable -- you can still call"
    age = max(0, now - updated)
    freq = doc.get("freq_mhz")
    on = f" on {freq:.3f} MHz" if isinstance(freq, (int, float)) and not isinstance(freq, bool) else ""
    if age > STALE_AFTER:
        return "off", f"{call} hasn't checked in since {_ago(age)} -- probably off the air"
    if doc.get("status") == "listening" and doc.get("busy") is True:
        return "busy", f"{call} is in a call right now -- try again in a few minutes"
    if doc.get("status") == "listening":
        return "listening", f"{call} is listening now{on}"
    return "off", f"{call} is off the air (updated {_ago(age)})"
