"""
Talk to a local Mercury modem through its VARA-style TNC interface:
a text control port (commands and status lines ending in CR) and a data
port next to it (base port + 1).

Messages on the data port use mercury-connector's framing, so a station
running mercury-connector understands them:

    4-byte little-endian length | filename "\\n" | payload

This module knows nothing about windows or buttons -- the app and the
tests both drive it.
"""

import queue
import re
import socket
import struct
import threading
import time

MAX_FRAME = 1024 * 1024          # nothing we expect is anywhere near this


class TncError(Exception):
    pass


def frame(name, payload):
    body = name.encode("utf-8") + b"\n" + payload
    return struct.pack("<I", len(body)) + body


class MercuryTnc:
    """One connection to the local Mercury. Status lines and received
    messages come out of .events as (kind, value) tuples:

        ("line", text)            every control line, for the log
        ("connected", peer)       ARQ link up
        ("disconnected", None)    link down
        ("snr", float)            what OUR modem hears from the other end
        ("bitrate", int)
        ("buffer", int)           bytes of ours the modem still has to send
        ("message", (name, bytes))
        ("closed", reason)        Mercury went away
    """

    def __init__(self, host="127.0.0.1", port=8300, timeout=5.0):
        self.host, self.port = host, int(port)
        self.events = queue.Queue()
        self.ctl = self.dat = None
        self.connected = False
        self.version = None
        self._stop = threading.Event()
        self._timeout = timeout

    # ------------------------------------------------------------ setup

    def open(self, mycall):
        try:
            self.ctl = socket.create_connection((self.host, self.port), timeout=self._timeout)
        except OSError as e:
            raise TncError(f"Mercury isn't answering on {self.host}:{self.port} -- is it running? ({e})")
        try:
            self.dat = socket.create_connection((self.host, self.port + 1), timeout=self._timeout)
        except OSError as e:
            self.ctl.close()
            raise TncError(f"Mercury's data port {self.port + 1} isn't answering ({e})")
        for s in (self.ctl, self.dat):
            s.settimeout(None)
        threading.Thread(target=self._read_ctl, daemon=True).start()
        threading.Thread(target=self._read_dat, daemon=True).start()
        busy = TncError("Mercury closed the connection. Another program (Winlink Express, Pat, "
                        "mercury-connector...) is probably using it -- close that first.")
        # same setup mercury-connector uses; we only call out, never listen
        try:
            for cmd in (f"MYCALL {mycall}", "LISTEN OFF", "PUBLIC OFF", "BW2300", "P2P SESSION", "VERSION"):
                self.command(cmd)
        except TncError:
            self.close()
            raise busy
        # a modem already in use by another program usually hangs up on us
        time.sleep(0.5)
        if self._stop.is_set():
            self.close()
            raise busy

    def close(self):
        self._stop.set()
        for s in (self.ctl, self.dat):
            if s:
                # shutdown first: a reader thread blocked in recv() keeps a
                # plain close() from actually ending the connection, and
                # Mercury would think we're still attached
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass

    # ------------------------------------------------------------ commands

    def command(self, text):
        try:
            self.ctl.sendall((text + "\r").encode("ascii", "replace"))
        except OSError as e:
            raise TncError(f"lost the connection to Mercury ({e})")

    def call(self, mycall, target):
        self.command(f"CONNECT {mycall} {target}")

    def hang_up(self):
        self.command("DISCONNECT")

    def send(self, name, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            self.dat.sendall(frame(name, payload))
        except OSError as e:
            raise TncError(f"lost Mercury's data port ({e})")

    # ------------------------------------------------------------ readers

    def _read_ctl(self):
        buf = b""
        try:
            while not self._stop.is_set():
                data = self.ctl.recv(4096)
                if not data:
                    break
                buf += data
                while b"\r" in buf or b"\n" in buf:
                    cut = min(i for i in (buf.find(b"\r"), buf.find(b"\n")) if i >= 0)
                    line, buf = buf[:cut].decode("ascii", "replace").strip(), buf[cut + 1:]
                    if line:
                        self._control_line(line)
        except OSError:
            pass
        if not self._stop.is_set():
            self._stop.set()
            self.events.put(("closed", "Mercury closed the control connection"))

    def _control_line(self, line):
        self.events.put(("line", line))
        if line.startswith("CONNECTED"):
            parts = line.split()
            self.connected = True
            self.events.put(("connected", parts[2] if len(parts) > 2 else ""))
        elif line == "DISCONNECTED":
            self.connected = False
            self.events.put(("disconnected", None))
        elif line.startswith("VERSION"):
            self.version = line[len("VERSION"):].strip() or None
        else:
            m = re.match(r"SN (-?\d+(?:\.\d+)?)$", line)
            if m:
                self.events.put(("snr", float(m.group(1))))
                return
            m = re.match(r"BITRATE \(\d+\) (\d+) BPS", line)
            if m:
                self.events.put(("bitrate", int(m.group(1))))
                return
            m = re.match(r"BUFFER (\d+)$", line)
            if m:
                self.events.put(("buffer", int(m.group(1))))

    def _recv_exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self.dat.recv(n - len(out))
            if not chunk:
                raise EOFError
            out += chunk
        return out

    def _read_dat(self):
        try:
            while not self._stop.is_set():
                (size,) = struct.unpack("<I", self._recv_exact(4))
                if size == 0 or size > MAX_FRAME:
                    raise EOFError
                body = self._recv_exact(size)
                name, _, payload = body.partition(b"\n")
                self.events.put(("message", (name.decode("utf-8", "replace"), payload)))
        except (OSError, EOFError, struct.error):
            pass
