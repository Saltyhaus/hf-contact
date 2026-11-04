"""
The list of stations you can call, published as stations.json (AG7JC's
is next to its live status). Settings offers it as a pick-list, and the
"List my station" form sends requests to whoever runs the list.

    {"updated": "2026-11-05T18:00:00Z",
     "submit": {"call": "AG7JC", "email": "joshua@ag7jc.com"},
     "stations": [{"call": "AG7JC", "freq_mhz": 14.105, "grid": "DM12",
                   "hours_utc": [1, 6], "when": "most evenings",
                   "echo_service": true, "worked": true,
                   "last_confirmed": "2026-11-03",
                   "status_url": "https://ag7jc.com/data/hf-contact.json"}]}

Only call is required. Anything malformed is dropped. The last list
downloaded is kept, so the pick-list still works with no internet
(shown with its date).
"""
import json
import re
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from .session import valid_call

DEFAULT_URL = "https://ag7jc.com/data/stations.json"
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$")
STALE_DAYS = 183


def _text(v, n=60):
    return " ".join(v.split())[:n] if isinstance(v, str) else ""


def _hours(v):
    if (isinstance(v, list) and len(v) == 2 and all(isinstance(h, int) and not isinstance(h, bool)
                                                    and 0 <= h <= 23 for h in v)):
        return [v[0], v[1]]
    return None


def _day(v):
    try:
        return date.fromisoformat(v).isoformat() if isinstance(v, str) else None
    except ValueError:
        return None


def parse(doc):
    """{"stations": [...], "submit": {...} or None, "updated": str or None}"""
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
        if _hours(it.get("hours_utc")):
            st["hours_utc"] = _hours(it.get("hours_utc"))
        if _text(it.get("when")):
            st["when"] = _text(it.get("when"))
        for flag in ("echo_service", "worked"):
            if it.get(flag) is True:
                st[flag] = True
        if _day(it.get("last_confirmed")):
            st["last_confirmed"] = _day(it.get("last_confirmed"))
        url = it.get("status_url")
        if isinstance(url, str) and url.startswith("https://") and len(url) < 300 and " " not in url:
            st["status_url"] = url
        out.append(st)
        if len(out) >= 300:
            break
    submit = None
    sub = doc.get("submit") if isinstance(doc, dict) else None
    if isinstance(sub, dict):
        call = _text(sub.get("call"), 12).upper()
        email = _text(sub.get("email"), 254)
        submit = {k: v for k, v in (("call", call if valid_call(call) else ""),
                                     ("email", email if EMAIL_RE.match(email) else "")) if v} or None
    updated = doc.get("updated") if isinstance(doc, dict) and isinstance(doc.get("updated"), str) else None
    return {"stations": out, "submit": submit, "updated": updated}


def fetch(url=DEFAULT_URL, timeout=6):
    """The parsed list, or None (no internet, no URL, not a list)."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hf-contact"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read(262144).decode("utf-8"))
    except Exception:
        return None
    return parse(doc) if isinstance(doc, dict) else None


def fetch_cached(url, cache_path, timeout=6, now=None):
    """Fresh list when there's internet (and save it), otherwise the last
    one saved for this URL with "cached_at" set, otherwise None."""
    cache = Path(cache_path)
    fresh = fetch(url, timeout)
    if fresh is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
            cache.write_text(json.dumps({"url": url, "saved": stamp, "list": fresh}), encoding="utf-8")
        except OSError:
            pass
        return fresh
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("url") != url or not isinstance(saved.get("list"), dict):
        return None
    lst = parse({"stations": saved["list"].get("stations"), "submit": saved["list"].get("submit"),
                 "updated": saved["list"].get("updated")})
    lst["cached_at"] = saved.get("saved") if isinstance(saved.get("saved"), str) else None
    return lst


def local_hours(hours_utc, offset_hours=None):
    """[13, 16] UTC -> (6, 9) in this computer's time zone."""
    if offset_hours is None:
        off = datetime.now().astimezone().utcoffset()
        offset_hours = round(off.total_seconds() / 3600) if off else 0
    return tuple(int((h + offset_hours) % 24) for h in hours_utc)


def hours_text(st, offset_hours=None):
    """'13:00–16:00 UTC (06:00–09:00 your time)', or the free text, or ''."""
    h = st.get("hours_utc")
    if h:
        a, b = local_hours(h, offset_hours)
        return f"{h[0]:02d}:00–{h[1]:02d}:00 UTC ({a:02d}:00–{b:02d}:00 your time)"
    return st.get("when", "")


def stale(st, today=None):
    d = st.get("last_confirmed")
    if not d:
        return False
    return ((today or date.today()) - date.fromisoformat(d)).days > STALE_DAYS


def label(st, today=None):
    """'AG7JC · 14.105 MHz · DM12 · echo service · worked on air'"""
    bits = [st["call"]]
    if st.get("freq_mhz"):
        bits.append(f"{st['freq_mhz']:.3f} MHz")
    if st.get("grid"):
        bits.append(st["grid"])
    if st.get("echo_service"):
        bits.append("echo service")
    bits.append("worked on air" if st.get("worked") else "listed")
    if stale(st, today):
        bits.append(f"not confirmed since {st['last_confirmed'][:7]}")
    return " · ".join(bits)


def as_settings(st, offset_hours=None):
    """The Settings fields for a station picked from the list. status_url is
    "" when the station publishes none, so another station's file can't stay."""
    freq = f"{st['freq_mhz']:.3f} MHz USB (dial)" if st.get("freq_mhz") else ""
    note = " · ".join(p for p in (st.get("grid", ""), hours_text(st, offset_hours),
                                  "echo service" if st.get("echo_service") else "") if p)
    return {"call": st["call"], "freq": freq, "note": note, "status_url": st.get("status_url", "")}
