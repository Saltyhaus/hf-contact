"""
The list of stations you can call, published next to the live status as
stations.json. Lets Settings offer a pick-list instead of typing a call.

    {"updated": "2026-11-05T18:00:00Z",
     "stations": [{"call": "AG7JC", "freq_mhz": 14.105, "grid": "DM12",
                   "when": "Most evenings", "echo_service": true,
                   "status_url": "https://ag7jc.com/data/hf-contact.json"}]}

Only call is required. Anything malformed is dropped; no internet means
an empty list and Settings works as before.
"""
import json
import re
import urllib.request

from .session import valid_call

DEFAULT_URL = "https://ag7jc.com/data/stations.json"
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")


def _text(v, n=60):
    return " ".join(v.split())[:n] if isinstance(v, str) else ""


def parse(doc):
    out, seen = [], set()
    items = doc.get("stations") if isinstance(doc, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        call = _text(it.get("call"), 12).upper()
        if not valid_call(call) or call in seen:
            continue
        seen.add(call)
        st = {"call": call}
        f = it.get("freq_mhz")
        if isinstance(f, (int, float)) and not isinstance(f, bool) and 1.8 <= f <= 54:
            st["freq_mhz"] = float(f)
        grid = _text(it.get("grid"), 6).upper()
        if GRID_RE.match(grid):
            st["grid"] = grid
        if _text(it.get("when")):
            st["when"] = _text(it.get("when"))
        if it.get("echo_service") is True:
            st["echo_service"] = True
        url = it.get("status_url")
        if isinstance(url, str) and url.startswith("https://") and len(url) < 300 and " " not in url:
            st["status_url"] = url
        out.append(st)
        if len(out) >= 200:
            break
    return out


def fetch(url=DEFAULT_URL, timeout=6):
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hf-contact"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse(json.loads(r.read(262144).decode("utf-8")))
    except Exception:
        return []


def label(st):
    """'AG7JC · 14.105 MHz · DM12 · echo service'"""
    bits = [st["call"]]
    if st.get("freq_mhz"):
        bits.append(f"{st['freq_mhz']:.3f} MHz")
    if st.get("grid"):
        bits.append(st["grid"])
    if st.get("echo_service"):
        bits.append("echo service")
    return " · ".join(bits)


def as_settings(st):
    """The Settings fields for a station picked from the list."""
    freq = f"{st['freq_mhz']:.3f} MHz USB (dial)" if st.get("freq_mhz") else ""
    note = " · ".join(p for p in (st.get("grid", ""), st.get("when", ""),
                                  "echo service" if st.get("echo_service") else "") if p)
    return {"call": st["call"], "freq": freq, "note": note, "status_url": st.get("status_url", "")}
