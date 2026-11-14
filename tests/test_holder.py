"""Naming the program that holds Mercury when it turns HF Contact away."""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from hf_contact import holder
from hf_contact.tnc import MercuryTnc, TncError

LINUX = os.path.exists("/proc/net/tcp")
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def row(local, remote, state, inode):
    return (f"   0: 0100007F:{local:04X} 0100007F:{remote:04X} {state} "
            f"00000000:00000000 00:00000000 00000000  1000        0 {inode} 1 0 20 4 30 10 -1\n")


def fake_proc(rows, procs):
    """procs: {pid: (comm, [args], [inodes])}"""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "net"))
    with open(os.path.join(root, "net", "tcp"), "w") as f:
        f.write(HEADER + "".join(rows))
    for pid, (comm, args, inodes) in procs.items():
        d = os.path.join(root, str(pid))
        os.makedirs(os.path.join(d, "fd"))
        with open(os.path.join(d, "comm"), "w") as f:
            f.write(comm + "\n")
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in args) + b"\0")
        for n, ino in enumerate(inodes):
            os.symlink(f"socket:[{ino}]", os.path.join(d, "fd", str(n + 3)))
        os.symlink("/dev/null", os.path.join(d, "fd", "0"))
    return root


class FromProc(unittest.TestCase):
    def test_finds_the_client_of_the_port(self):
        root = fake_proc(
            [row(8300, 0, "0A", 100),          # Mercury listening
             row(8300, 44250, "01", 101),      # Mercury's end
             row(44250, 8300, "01", 102),      # the connector's end
             row(37362, 8301, "01", 103)],     # data port, ignored
            {500: ("mercury-connect", ["/home/x/mercury-connector/mercury-connector", "-x", "mercury"], [102, 103]),
             600: ("mercury", ["mercury", "-p", "8300"], [100, 101]),
             700: ("bash", ["bash"], [])})
        self.assertEqual(holder.who_holds(8300, proc=root, own_pid=1), [("mercury-connector", 500)])
        self.assertEqual(holder.who_holds(9999, proc=root, own_pid=1), [])

    def test_names(self):
        root = fake_proc(
            [row(40001, 8300, "01", 201), row(40002, 8300, "01", 202), row(40003, 8300, "01", 203),
             row(40004, 8300, "06", 204)],                      # TIME_WAIT: not counted
            {11: ("pat", ["/usr/bin/pat", "http"], [201]),
             12: ("python3", ["python3", "/home/x/hf-contact/hf-contact.py"], [202]),
             13: ("wsjtx", ["wsjtx"], [203]),
             14: ("ghost", ["ghost"], [204])})
        got = holder.who_holds(8300, proc=root, own_pid=12)            # 12 is "us"
        self.assertEqual(got, [("Pat", 11), ("wsjtx", 13)])
        self.assertEqual(holder.who_holds(8300, proc=root, own_pid=1)[1], ("HF Contact", 12))

    def test_no_proc(self):
        self.assertEqual(holder.who_holds(8300, proc="/nonexistent"), [])


class Messages(unittest.TestCase):
    def test_texts(self):
        self.assertIn("Pause mail", holder.busy_message([("mercury-connector", 5)]))
        self.assertIn("systemctl stop mercury-connector", holder.busy_message([("mercury-connector", 5)]))
        self.assertIn("Close Pat", holder.busy_message([("Pat", 5)]))
        self.assertIn("another HF Contact", holder.busy_message([("HF Contact", 5)]))
        self.assertIn("wsjtx (pid 9)", holder.busy_message([("wsjtx", 9)]))
        generic = holder.busy_message([])
        for word in ("Winlink Express", "Pat", "mercury-connector", "Pause mail"):
            self.assertIn(word, generic)


class FakeBusyMercury:
    """Listens like Mercury 1.9.24: the first program keeps the ports,
    anyone after it is accepted and dropped at once."""

    def __init__(self):
        self.ctl = socket.socket()
        self.ctl.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        while True:
            self.ctl.bind(("127.0.0.1", 0))
            self.port = self.ctl.getsockname()[1]
            self.dat = socket.socket()
            try:
                self.dat.bind(("127.0.0.1", self.port + 1))
                break
            except OSError:
                self.dat.close()
                self.ctl.close()
                self.ctl = socket.socket()
        self.held = []
        for s in (self.ctl, self.dat):
            s.listen(4)
            threading.Thread(target=self._serve, args=(s,), daemon=True).start()

    def _serve(self, srv):
        first = True
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            if first:
                self.held.append(c)
                first = False
            else:
                c.close()

    def close(self):
        for s in [self.ctl, self.dat] + self.held:
            s.close()


@unittest.skipUnless(LINUX, "Linux only")
class Refused(unittest.TestCase):
    def setUp(self):
        self.m = FakeBusyMercury()
        # another program takes Mercury first; argv[0] makes it look like the connector
        code = ("import socket,sys,time; s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); "
                "d=socket.create_connection(('127.0.0.1',int(sys.argv[1])+1)); print('up',flush=True); time.sleep(30)")
        self.other = subprocess.Popen(["mercury-connector", "-c", code, str(self.m.port)],
                                      executable=sys.executable, stdout=subprocess.PIPE, text=True)
        self.assertEqual(self.other.stdout.readline().strip(), "up")

    def tearDown(self):
        self.other.kill()
        self.other.wait()
        self.m.close()

    def test_message_names_the_connector(self):
        tnc = MercuryTnc("127.0.0.1", self.m.port)
        start = time.time()
        with self.assertRaises(TncError) as cm:
            tnc.open("K1ABC")
        self.assertIn("in use by mercury-connector", str(cm.exception))     # found, not the fallback
        self.assertIn("Pause mail", str(cm.exception))
        self.assertLess(time.time() - start, 10)


class Layout(unittest.TestCase):
    def test_text_sizes(self):
        from hf_contact import scroll
        self.assertEqual(scroll.text_scale("Large"), 1.2)
        self.assertEqual(scroll.text_scale("small"), 0.9)
        self.assertEqual(scroll.text_scale(None), 1.0)
        self.assertEqual(scroll.text_scale("huge"), 1.0)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
    def test_fit_to_screen(self):
        import tkinter as tk
        from hf_contact import scroll
        root = tk.Tk()
        try:
            sh = root.winfo_screenheight()
            w, h = scroll.fit_to_screen(root, 800, sh + 500)
            self.assertLessEqual(h, sh - 90)
            w, h = scroll.fit_to_screen(root, 800, 600, saved="900x500")
            self.assertEqual((w, h), (900, 500))
            w, h = scroll.fit_to_screen(root, 800, 600, saved="nonsense")
            self.assertEqual(w, 800)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
