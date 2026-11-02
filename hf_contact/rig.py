"""
Read the radio's dial frequency from rigctld (Hamlib's network daemon),
so the echo service's segment check uses where the radio really is.
Optional: no address set, or rigctld not there, means None.
"""
import socket


def parse_addr(text, default_port=4532):
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return "127.0.0.1", int(text)          # just a port: rigctld on this computer
    host, _, port = text.rpartition(":") if ":" in text else (text, "", "")
    try:
        return (host or "127.0.0.1"), int(port) if port else default_port
    except ValueError:
        return None


def read_mhz(addr_text, timeout=2.0):
    addr = parse_addr(addr_text)
    if not addr:
        return None
    try:
        with socket.create_connection(addr, timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"f\n")
            data = b""
            while b"\n" not in data and len(data) < 64:
                chunk = s.recv(64)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    try:
        hz = int(data.split(b"\n")[0].strip())
    except ValueError:
        return None             # "RPRT -n": rigctld up, radio not answering
    return hz / 1e6 if 1_000_000 <= hz <= 60_000_000 else None
