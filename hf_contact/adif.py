"""
Export HF Contact's History as ADIF, so the contacts can go into a
logbook (QLog, Wavelog, Log4OM, ...).

Only entries that recorded a contact are exported: echo tests you ran,
calls where you sent a message or got a reply, and echo tests your own
echo service answered. Older History (before 0.5.0) has no contact data
and is skipped.

Mercury has no ADIF mode of its own, so contacts are logged as
MODE DYNAMIC (ADIF's mode for adaptive ARQ modems such as VARA), with
"Mercury" in the comment.
"""

from datetime import datetime, timezone

from . import __version__

BANDS = (("160m", 1.8, 2.0), ("80m", 3.5, 4.0), ("60m", 5.06, 5.45), ("40m", 7.0, 7.3),
         ("30m", 10.1, 10.15), ("20m", 14.0, 14.35), ("17m", 18.068, 18.168), ("15m", 21.0, 21.45),
         ("12m", 24.89, 24.99), ("10m", 28.0, 29.7), ("6m", 50.0, 54.0))
MODE = "DYNAMIC"


def band_for(mhz):
    if mhz is None:
        return None
    for name, lo, hi in BANDS:
        if lo <= mhz <= hi:
            return name
    return None


def _field(name, value):
    value = str(value)
    return f"<{name}:{len(value.encode('utf-8'))}>{value} "


def _clean(text):
    return " ".join(str(text).replace("<", "(").replace(">", ")").split())


def qso(call, mycall, utc=None, mhz=None, grid="", comment=""):
    """A contact for a History entry. utc: aware datetime (default now)."""
    utc = utc or datetime.now(timezone.utc)
    q = {"call": call.upper(), "mycall": mycall.upper(), "utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if mhz:
        q["mhz"] = round(float(mhz), 6)
    if grid:
        q["grid"] = grid.upper()
    if comment:
        q["comment"] = comment
    return q


def record(q):
    when = datetime.strptime(q["utc"], "%Y-%m-%dT%H:%M:%SZ")
    out = _field("CALL", q["call"]) + _field("QSO_DATE", when.strftime("%Y%m%d")) + \
        _field("TIME_ON", when.strftime("%H%M%S")) + _field("MODE", MODE)
    band = band_for(q.get("mhz"))
    if band:
        out += _field("BAND", band) + _field("FREQ", f"{q['mhz']:.6f}")
    out += _field("STATION_CALLSIGN", q["mycall"])
    if q.get("grid"):
        out += _field("MY_GRIDSQUARE", q["grid"])
    comment = "Mercury" + (f": {_clean(q['comment'])}" if q.get("comment") else "")
    out += _field("COMMENT", comment)
    return out + "<EOR>\n"


def export(history, now=None):
    """(adif_text, count) for the History entries that carry a contact."""
    now = now or datetime.now(timezone.utc)
    head = (f"HF Contact {__version__} ADIF export\n" + _field("ADIF_VER", "3.1.4") +
            _field("PROGRAMID", "HF Contact") + _field("PROGRAMVERSION", __version__) +
            _field("CREATED_TIMESTAMP", now.strftime("%Y%m%d %H%M%S")) + "<EOH>\n")
    rows = [record(h["qso"]) for h in history if isinstance(h.get("qso"), dict) and h["qso"].get("call")]
    return head + "".join(rows), len(rows)
