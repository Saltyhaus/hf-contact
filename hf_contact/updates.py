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


# ------------------------------------------------------------ Update now

EXE_URL = "https://github.com/Saltyhaus/hf-contact/releases/latest/download/HF-Contact.exe"
ZIP_URL = "https://github.com/Saltyhaus/hf-contact/archive/refs/tags/v{v}.zip"


def app_root():
    """The folder HF Contact runs from (the clone, or the unzipped download)."""
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def how(root=None, frozen=None):
    """'exe' (the Windows .exe), 'git' (a git clone) or 'zip' (an unzipped
    download) -- decides how Update now replaces the program."""
    import sys
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return "exe"
    root = root or app_root()
    return "git" if (root / ".git").exists() else "zip"


def _run(cmd, cwd):
    import subprocess
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def _get(url, timeout=60, limit=60 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": "hf-contact"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise OSError("the download is bigger than expected")
    return data


def install(version, root=None, mode=None):
    """Put release `version` in place of this one. (ok, message). Nothing is
    changed unless the new copy arrived whole; the old one is kept:
    git -- the clone moves forward (local edits stop it, nothing lost);
    zip -- hf_contact/ is swapped, the old one kept as hf_contact.old;
    exe -- the new .exe goes next to this one, which is renamed .old."""
    import io
    import os
    import shutil
    import sys
    import zipfile
    root = root or app_root()
    mode = mode or how(root)
    if not parse(version):
        return False, f"not a version: {version!r}"
    if mode == "git":
        rc, out = _run(["git", "fetch", "--tags", "origin"], root)
        if rc != 0:
            return False, "git fetch didn't work: " + (out.splitlines() or ["no answer"])[-1]
        rc, out = _run(["git", "merge", "--ff-only", "origin/main"], root)
        if rc != 0:
            return False, ("this copy has its own changes, so it wasn't touched -- update it by hand "
                           "(git pull): " + (out.splitlines() or [""])[-1])
        return True, f"updated to {version} (git)"
    if mode == "exe":
        exe = os.path.abspath(sys.executable)
        new, old = exe + ".new", exe + ".old"
        try:
            data = _get(EXE_URL)
            if not data.startswith(b"MZ"):
                return False, "the download isn't a Windows program"
            with open(new, "wb") as f:
                f.write(data)
            if os.path.exists(old):
                os.remove(old)
            os.replace(exe, old)                  # Windows lets a running .exe be renamed
            os.replace(new, exe)
        except OSError as e:
            return False, f"couldn't put the new .exe in place: {e}"
        return True, f"updated to {version}; the old one is {os.path.basename(old)}"
    try:
        z = zipfile.ZipFile(io.BytesIO(_get(ZIP_URL.format(v=version))))
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"couldn't download {version}: {e}"
    names = z.namelist()
    top = names[0].split("/")[0] + "/" if names else ""
    init = top + "hf_contact/__init__.py"
    if init not in names or f'__version__ = "{version}"' not in z.read(init).decode("utf-8", "replace"):
        return False, "the download doesn't look like HF Contact " + version
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hf-contact-")
    try:
        for n in names:
            rel = n[len(top):]
            if not rel or rel.endswith("/") or ".." in rel.split("/") or rel.startswith("/"):
                continue
            dest = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(z.read(n))
        pkg, old = root / "hf_contact", root / "hf_contact.old"
        if old.exists():
            shutil.rmtree(old)
        os.replace(pkg, old)
        shutil.copytree(os.path.join(tmp, "hf_contact"), pkg)
        for rel in ("hf-contact.py", "install.sh", "README.md", "assets/icon.png", "assets/icon.ico"):
            src = os.path.join(tmp, rel)
            if os.path.exists(src):
                os.makedirs(root / os.path.dirname(rel), exist_ok=True) if os.path.dirname(rel) else None
                shutil.copy2(src, root / rel)
    except OSError as e:
        return False, f"couldn't put the new files in place: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True, f"updated to {version}; the old program is in hf_contact.old"


def restart_cmd():
    """How to start HF Contact again the way it was started."""
    import sys
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and spec.name.startswith("hf_contact"):
        return [sys.executable, "-m", "hf_contact"]
    return [sys.executable, str(app_root() / "hf-contact.py")]
