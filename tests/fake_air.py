"""
Two fake Mercury modems joined by a fake radio path, for testing.

Each "station" is a VARA-style TNC (control port P, data port P+1) like
Mercury's. CONNECT from one side links them: both get CONNECTED, then
bytes written to one data port come out of the other, with BUFFER n /
BUFFER 0 reported to the sender and SN / BITRATE lines to both, the way
Mercury reports them. DISCONNECT from either side ends it for both.
"""

import socket
import threading
import time


class Station:
    def __init__(self, air, port, snr):
        self.air, self.port, self.snr = air, port, snr
        self.mycall = None
        self.listen = True
        self.ctl = self.dat = None
        self.lines = []          # commands received
        self.ready = threading.Event()
        self.ctl_srv = self._srv(port)
        self.dat_srv = self._srv(port + 1)
        threading.Thread(target=self._accept, daemon=True).start()

    @staticmethod
    def _srv(port):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(4)
        return s

    def _accept(self):
        while True:
            ctl, _ = self.ctl_srv.accept()
            if self.ctl is not None and self.air.single_client:
                ctl.close()               # busy: one program at a time, like a real modem
                continue
            dat, _ = self.dat_srv.accept()
            self.ctl, self.dat = ctl, dat
            self.ready.set()
            threading.Thread(target=self._read_ctl, args=(ctl,), daemon=True).start()
            threading.Thread(target=self._read_dat, args=(dat,), daemon=True).start()

    def send(self, line):
        try:
            self.ctl.sendall((line + "\r").encode())
        except (OSError, AttributeError):
            pass

    def _read_ctl(self, ctl):
        buf = b""
        try:
            while True:
                try:
                    data = ctl.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    self._command(line.decode())
        finally:
            if self.ctl is ctl:           # that program is gone: free the modem
                self.ctl = None
                if self.air.link and self in self.air.link:
                    self.air.hang_up()

    def _command(self, line):
        self.lines.append(line)
        if line.startswith("MYCALL "):
            self.mycall = line.split()[1]
            self.send("OK")
        elif line == "LISTEN OFF":
            self.listen = False
            self.send("OK")
        elif line == "LISTEN ON":
            self.listen = True
            self.send("OK")
        elif line == "VERSION":
            self.send("VERSION 1.9.13-fake")
        elif line.startswith("CONNECT "):
            _, src, dst = line.split()[:3]
            self.air.call(self, src, dst)
        elif line == "DISCONNECT":
            self.air.hang_up()
        else:
            self.send("OK")

    def _read_dat(self, dat):
        while True:
            try:
                data = dat.recv(65536)
            except OSError:
                return
            if not data:
                return
            self.air.carry(self, data)


class FakeAir:
    def __init__(self, port_a, port_b, snr_a=6.2, snr_b=4.0, bitrate=1400, answer=True):
        self.single_client = True
        self.a = Station(self, port_a, snr_a)   # snr_a: how A hears B
        self.b = Station(self, port_b, snr_b)
        self.bitrate = bitrate
        self.answer = answer
        self.link = None
        self.carried = 0
        self.lock = threading.Lock()

    def other(self, st):
        return self.b if st is self.a else self.a

    def call(self, caller, src, dst):
        callee = self.other(caller)
        def go():
            time.sleep(0.5)
            if not self.answer or not callee.listen or (callee.mycall or "").upper() != dst.upper():
                time.sleep(1)
                caller.send("DISCONNECTED")
                return
            callee.send("PENDING")
            time.sleep(0.3)
            self.link = (caller, callee)
            caller.send(f"CONNECTED {src} {dst} 2300")
            callee.send(f"CONNECTED {src} {dst} 2300")
            for st in (caller, callee):
                st.send(f"SN {st.snr:.1f}")
                st.send(f"BITRATE (3) {self.bitrate} BPS")
        threading.Thread(target=go, daemon=True).start()

    def hang_up(self):
        if self.link:
            self.link = None
            for st in (self.a, self.b):
                st.send("DISCONNECTED")

    def carry(self, sender, data):
        if not self.link:
            return                       # nobody to hear it
        with self.lock:
            sender.send(f"BUFFER {len(data)}")
            time.sleep(0.2)
            try:
                self.other(sender).dat.sendall(data)
            except OSError:
                return
            self.carried += len(data)
            sender.send("BUFFER 0")
