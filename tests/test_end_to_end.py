"""Visitor app <-> fake air <-> real mercury-connector (guest mode) at home.

    CONNECTOR=/path/to/mercury-connector python3 -m unittest tests.test_end_to_end
Skipped when CONNECTOR isn't set.
"""
import glob
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from tests.fake_air import FakeAir
from hf_contact.session import run_session, compose_message

CONNECTOR = os.environ.get("CONNECTOR")
HOME, VISITOR = 18400, 18410


@unittest.skipUnless(CONNECTOR, "set CONNECTOR to a mercury-connector binary built with guest mode")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="hfc_e2e_")
        cls.out = os.path.join(cls.work, "outgoing") + "/"
        cls.inb = os.path.join(cls.work, "incoming") + "/"
        os.makedirs(cls.out)
        os.makedirs(cls.inb)
        cls.air = FakeAir(HOME, VISITOR, snr_a=6.2, snr_b=4.0, bitrate=1400)
        cls.log = open(os.path.join(cls.work, "connector.log"), "w")
        cls.proc = subprocess.Popen([CONNECTOR, "-x", "mercury", "-i", cls.out, "-o", cls.inb, "-c", "AG7JC",
                                     "-d", "AG7JC-2,AG7JC-3", "-a", "127.0.0.1", "-p", str(HOME),
                                     "-t", "600", "-g"], stdout=cls.log, stderr=cls.log)
        assert cls.air.a.ready.wait(10), "connector never attached"
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        if not os.environ.get("KEEP"):
            shutil.rmtree(cls.work, ignore_errors=True)
        else:
            print("kept", cls.work)

    def session(self, **kw):
        cfg = {"mycall": kw.pop("mycall", "K1ABC"), "target": kw.pop("target", "AG7JC"),
               "host": "127.0.0.1", "port": VISITOR, "grid": "FN42"}
        steps = []
        r = run_session(cfg, progress=lambda k, t: steps.append((k, t)), idle_after=3,
                        connect_timeout=15, echo_timeout=20, **kw)
        time.sleep(1.5)   # let both modems settle before the next test
        return r, steps

    def test_1_echo(self):
        r, steps = self.session(echo=True)
        self.assertEqual(r.error, "")
        self.assertTrue(r.linked)
        self.assertIn("ECHO K1ABC de AG7JC", r.echo_reply)
        self.assertIn("SNR +6.2 dB, speed 1400 bps", r.echo_reply)   # how home heard us
        self.assertIn("Echo test from K1ABC in FN42", r.echo_reply)
        self.assertEqual(r.heard_snr, 4.0)                            # how we heard home
        self.assertIn(("info", "Mercury 1.9.13-fake"), steps)

    def test_2_message(self):
        before = set(glob.glob(self.inb + "guest_K1ABC_*"))
        r, _ = self.session(message=compose_message("Hello from the website!", "Pat", "fn42"))
        self.assertEqual(r.error, "")
        self.assertTrue(r.message_sent)
        new = set(glob.glob(self.inb + "guest_K1ABC_*")) - before
        self.assertEqual(len(new), 1)
        text = open(new.pop()).read()
        self.assertTrue(text.startswith("type: report\ncallsign: K1ABC\ncategory: status\nguest: yes\n\n"))
        self.assertIn("Hello from the website!\n\n-- Pat, FN42", text)

    def test_3_reply_waiting(self):
        d = os.path.join(self.out, "K1ABC")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "reply_7.txt")
        with open(p, "w") as f:
            f.write("type: reply\ncallsign: K1ABC\n\nThanks for the call, 73 Joshua")
        old = time.time() - 10
        os.utime(p, (old, old))
        r, _ = self.session()
        self.assertEqual(r.error, "")
        self.assertEqual(r.replies, [("reply_7.txt", "Thanks for the call, 73 Joshua")])
        for _ in range(20):
            if not os.path.exists(p):
                break
            time.sleep(0.25)
        self.assertFalse(os.path.exists(p), "delivered reply should be deleted at home")

    def test_4_wrong_station(self):
        r, _ = self.session(target="AG7XX", echo=True)
        self.assertFalse(r.linked)
        self.assertIn("didn't answer", r.error)

    def test_5_mercury_busy(self):
        # another program (Winlink Express, Pat...) already has the visitor's modem
        hog = socket.create_connection(("127.0.0.1", VISITOR))
        hog_d = socket.create_connection(("127.0.0.1", VISITOR + 1))
        try:
            time.sleep(0.5)
            r, _ = self.session(echo=True)
        finally:
            hog.shutdown(socket.SHUT_RDWR)
            hog.close()
            hog_d.close()
        self.assertFalse(r.linked)
        self.assertIn("Another program", r.error)

    def test_6_mercury_not_running(self):
        cfg = {"mycall": "K1ABC", "target": "AG7JC", "host": "127.0.0.1", "port": 18490}
        r = run_session(cfg, echo=True)
        self.assertIn("isn't answering", r.error)


if __name__ == "__main__":
    unittest.main()
