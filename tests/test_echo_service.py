"""The echo service: pure helpers, then HF Contact calling HF Contact
over the fake radio path (tests/fake_air.py)."""

import socket
import threading
import time
import unittest
from datetime import datetime, timezone

from hf_contact.echo_service import (EchoService, echo_reply, in_schedule, notice_text, parse_mhz,
                                     segment_check, NOTICE_NAME)
from hf_contact.session import echo_summary, run_session
from hf_contact.tnc import MercuryTnc
from tests.fake_air import FakeAir


def free_port_pair():
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p < 65000:
            try:
                t = socket.socket()
                t.bind(("127.0.0.1", p + 1))
                t.close()
                return p
            except OSError:
                continue


class Helpers(unittest.TestCase):
    def test_reply_matches_connector_format(self):
        when = datetime(2026, 11, 1, 18, 5, tzinfo=timezone.utc)
        self.assertEqual(echo_reply("K1ABC", "N0CALL", 4.0, 1400, b"Echo test from K1ABC in FN42", when),
                         "ECHO K1ABC de N0CALL\nCopied you at 2026-11-01 18:05Z\n"
                         "Your signal here: SNR +4.0 dB, speed 1400 bps\n"
                         "You sent: Echo test from K1ABC in FN42\n73\n")
        r = echo_reply("K1ABC", "N0CALL", -1.54, None, b"  \r\n", when)
        self.assertIn("SNR -1.5 dB, speed not reported", r)
        self.assertIn("You sent: (empty ping)", r)
        self.assertIn("You sent: a b c\n", echo_reply("K", "N", None, None, b"a\nb\tc\x07", when))
        self.assertEqual(echo_summary(r), "SNR -1.5 dB, speed not reported")
        self.assertEqual(len(echo_reply("K", "N", 1, 1, b"x" * 500, when).split("You sent: ")[1].split("\n")[0]), 120)

    def test_segments(self):
        for dial in (14.101, 14.105, 14.109, 7.100, 7.102, 10.140, 3.585, 21.090):
            self.assertTrue(segment_check(dial)[0], dial)
        for dial in (14.0975, 14.110, 14.074, 7.103, 7.074, 14.0995):
            ok, text = segment_check(dial)
            self.assertFalse(ok, dial)
            self.assertIn("outside the automatic control segment in the US", text)
        self.assertEqual(segment_check(None), (None, ""))

    def test_parse_mhz(self):
        self.assertEqual(parse_mhz("14.105"), 14.105)
        self.assertEqual(parse_mhz("14.105 MHz USB (dial)"), 14.105)
        self.assertEqual(parse_mhz("7102 kHz"), 7.102)
        self.assertEqual(parse_mhz("14105"), 14.105)
        self.assertIsNone(parse_mhz(""))
        self.assertIsNone(parse_mhz("dial"))

    def test_schedule(self):
        at = lambda h: datetime(2026, 11, 1, h, 30)
        self.assertTrue(in_schedule({}, at(3)))
        day = {"echo_schedule": "hours", "echo_from": 8, "echo_to": 20}
        self.assertTrue(in_schedule(day, at(8)))
        self.assertTrue(in_schedule(day, at(19)))
        self.assertFalse(in_schedule(day, at(20)))
        self.assertFalse(in_schedule(day, at(2)))
        night = {"echo_schedule": "hours", "echo_from": 22, "echo_to": 6}
        self.assertTrue(in_schedule(night, at(23)))
        self.assertTrue(in_schedule(night, at(5)))
        self.assertFalse(in_schedule(night, at(12)))
        self.assertTrue(in_schedule({"echo_schedule": "hours", "echo_from": 5, "echo_to": 5}, at(12)))


class OverTheAir(unittest.TestCase):
    """Station A: the normal app calling. Station B: the echo service."""

    def setUp(self):
        self.pa, self.pb = free_port_pair(), None
        while self.pb is None or abs(self.pb - self.pa) < 2:
            self.pb = free_port_pair()
        self.air = FakeAir(self.pa, self.pb, snr_a=6.2, snr_b=4.0, bitrate=1400)
        self.events = []
        self.svc = None

    def tearDown(self):
        if self.svc:
            self.svc.stop()
            self.svc.join(15)

    def start_service(self, **kw):
        self.svc = EchoService({"mycall": "N0CALL", "host": "127.0.0.1", "port": self.pb},
                               progress=lambda k, v: self.events.append((k, v)), **kw)
        self.svc.start()
        end = time.time() + 5
        while not any(k == "svc-status" for k, _ in self.events) and time.time() < end:
            time.sleep(0.05)
        self.assertIn("LISTEN ON", self.air.b.lines)

    def call(self, **kw):
        cfg = {"mycall": "K1ABC", "target": "N0CALL", "host": "127.0.0.1", "port": self.pa, "grid": "fn42"}
        return run_session(cfg, connect_timeout=15, idle_after=2, echo_timeout=20, **kw)

    def kinds(self, kind):
        return [v for k, v in self.events if k == kind]

    def test_echo_answered(self):
        self.start_service()
        r = self.call(echo=True)
        self.assertTrue(r.linked, r.error)
        self.assertEqual(r.error, "")
        self.assertTrue(r.echo_reply.startswith("ECHO K1ABC de N0CALL"), r.echo_reply)
        self.assertEqual(echo_summary(r.echo_reply), "SNR +4.0 dB, speed 1400 bps")
        self.assertIn("You sent: Echo test from K1ABC in FN42", r.echo_reply)
        self.assertEqual(self.kinds("svc-linked"), ["K1ABC"])
        self.assertEqual(self.kinds("svc-echo"), [{"call": "K1ABC", "snr": 4.0, "bps": 1400}])

    def test_messages_dropped_with_one_notice(self):
        self.start_service()
        r = self.call(echo=True, message="please keep this")
        self.assertTrue(r.echo_reply)
        self.assertEqual(len(r.replies), 1)
        self.assertEqual(r.replies[0][0], NOTICE_NAME)
        self.assertEqual(r.replies[0][1], notice_text("N0CALL").strip())
        self.assertEqual(self.kinds("svc-ignored"), ["K1ABC"])
        # nothing it received is held anywhere in the service's events
        self.assertFalse(any("please keep this" in str(v) for _, v in self.events))

    def test_three_echoes_per_link_and_idle_hang_up(self):
        self.start_service(idle_after=3)
        tnc = MercuryTnc("127.0.0.1", self.pa)
        tnc.open("K1ABC")
        tnc.call("K1ABC", "N0CALL")
        replies, start, gone = 0, time.time(), False
        sent = False
        while time.time() - start < 20:
            try:
                kind, val = tnc.events.get(timeout=0.5)
            except Exception:
                continue
            if kind == "connected" and not sent:
                for i in range(4):
                    tnc.send(f"echo{i}.txt", f"ping {i}")
                sent = True
            elif kind == "message":
                replies += 1
            elif kind == "disconnected":
                gone = True
                break
        tnc.close()
        self.assertEqual(replies, 3)
        self.assertTrue(gone, "the service should hang up an idle link")
        self.assertEqual(len(self.kinds("svc-echo")), 3)

    def test_hourly_limit_per_caller(self):
        self.start_service(links_per_hour=1)
        self.assertTrue(self.call(echo=True).echo_reply)
        r = self.call(echo=True)
        self.assertFalse(r.echo_reply)
        self.assertEqual(self.kinds("svc-refused"), ["K1ABC"])

    def test_stop_frees_mercury(self):
        self.start_service()
        self.svc.stop()
        self.svc.join(10)
        self.assertFalse(self.svc.running)
        self.assertEqual(self.events[-1], ("svc-stopped", None))
        end = time.time() + 5
        while self.air.b.ctl is not None and time.time() < end:
            time.sleep(0.05)
        self.assertIsNone(self.air.b.ctl, "the modem should be free for the app's own calls")
        self.assertFalse(self.air.b.listen, "Mercury must stop answering calls once the service stops")
        r = self.call(echo=True)
        self.assertFalse(r.linked, "nobody should answer after the service stopped")

    def test_listen_off_after_a_crash(self):
        # a service that died without stopping leaves Mercury listening
        from hf_contact.echo_service import listen_off
        from hf_contact.tnc import MercuryTnc
        tnc = MercuryTnc("127.0.0.1", self.pb)
        tnc.open("N0CALL", listen=True)
        tnc.close()                      # no LISTEN OFF: like a crash
        end = time.time() + 5
        while self.air.b.ctl is not None and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(self.air.b.listen)
        self.assertTrue(listen_off({"host": "127.0.0.1", "port": self.pb}))
        self.assertFalse(self.air.b.listen)
        self.assertEqual(self.air.b.mycall, "N0CALL", "the cleanup leaves the callsign alone")

    def test_listen_off_without_mercury(self):
        from hf_contact.echo_service import listen_off
        self.assertFalse(listen_off({"host": "127.0.0.1", "port": 1}, timeout=1))


if __name__ == "__main__":
    unittest.main()
