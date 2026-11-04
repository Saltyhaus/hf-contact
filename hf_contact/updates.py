"""
Is there a newer HF Contact? Once a day the app asks GitHub for the
latest release and, if it's newer, shows a quiet line with a link.
No internet, or GitHub not answering: nothing shown, nothing asked.
"""
import json
import re
import urllib.request

LATEST_API = "https://api.github.com/repos/Saltyhaus/hf-contact/releases/latest"
RELEASES_PAGE = "https://github.com/Saltyhaus/hf-contact/releases/latest"
DAY = 24 * 3600
TAG_RE = re.compile(r"^v?(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def parse(tag):
    m = TAG_RE.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest_tag, current):
    """'0.5.1' if latest_tag is newer than current, else None."""
    a, b = parse(latest_tag), parse(current)
    if not a or not b or a <= b:
        return None
    return ".".join(map(str, a))


def check(current, url=LATEST_API, timeout=6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hf-contact",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read(262144).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or doc.get("draft") or doc.get("prerelease"):
        return None
    return newer(doc.get("tag_name"), current)
