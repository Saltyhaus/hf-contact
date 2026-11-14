"""
Who is using Mercury?

Mercury 1.9.24 and newer serve one program at a time on their TNC ports
and turn the rest away. When HF Contact is turned away, this finds the
program holding Mercury so the message can name it.

Linux only, and only programs run by this user: their sockets are in
/proc. Elsewhere (Windows, or a service run by another user) it finds
nothing, and the message falls back to the usual suspects.
"""

import os

_TABLES = ("net/tcp", "net/tcp6")


def _client_inodes(port, proc):
    """Socket inodes of established connections whose far end is `port`:
    the client side of links to the program listening on it."""
    inodes = set()
    for table in _TABLES:
        try:
            with open(os.path.join(proc, table)) as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            parts = row.split()
            if len(parts) < 10 or parts[3] != "01":          # 01 = ESTABLISHED
                continue
            try:
                if int(parts[2].rsplit(":", 1)[1], 16) == port and parts[9] != "0":
                    inodes.add(parts[9])
            except (IndexError, ValueError):
                continue
    return inodes


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def _name(pid, proc):
    comm = _read(os.path.join(proc, pid, "comm")).decode("utf-8", "replace").strip()
    args = [a.decode("utf-8", "replace") for a in _read(os.path.join(proc, pid, "cmdline")).split(b"\0") if a]
    line = " ".join(args).lower()
    if "mercury-connector" in line or comm.startswith("mercury-connect"):
        return "mercury-connector"
    if "hf-contact" in line or "hf_contact" in line:
        return "HF Contact"
    if comm == "pat" or (args and os.path.basename(args[0]) == "pat"):
        return "Pat"
    return comm or (os.path.basename(args[0]) if args else "")


def who_holds(port, proc="/proc", own_pid=None):
    """[(name, pid)] of this user's programs connected to `port`, not
    counting this one. [] if none can be seen."""
    inodes = _client_inodes(port, proc)
    if not inodes:
        return []
    own = str(own_pid if own_pid is not None else os.getpid())
    want = {f"socket:[{i}]" for i in inodes}
    found = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    for pid in pids:
        if pid == own:
            continue
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:                  # not ours, or gone
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) in want:
                    found.append((_name(pid, proc) or f"pid {pid}", int(pid)))
                    break
            except OSError:
                continue
    return sorted(set(found), key=lambda x: x[1])


def busy_message(holders):
    """What to tell the operator when Mercury turned HF Contact away."""
    names = [n for n, _ in holders]
    if "mercury-connector" in names:
        return ("Mercury is in use by mercury-connector. If OffGrid Comms started it, press "
                "Pause mail on its HF Mail tab, then try again. If it runs as a service: "
                "sudo systemctl stop mercury-connector.")
    if "Pat" in names:
        return "Mercury is in use by Pat. Close Pat (or stop pat http), then try again."
    if "HF Contact" in names:
        return ("Mercury is in use by another HF Contact window (maybe its echo service). "
                "Close that one, then try again.")
    if holders:
        who = ", ".join(f"{n} (pid {p})" for n, p in holders)
        return f"Mercury is in use by {who}. Close it, then try again."
    return ("Mercury turned HF Contact away: another program is using it. Mercury serves one "
            "program at a time: close Winlink Express, Pat, mercury-connector (in OffGrid Comms: "
            "Pause mail) or any other client first.")
