import unittest

from hf_contact.session import compose_message, echo_summary, reply_body, valid_call
from hf_contact.tnc import frame


class Helpers(unittest.TestCase):
    def test_valid_call(self):
        for c in ("K1ABC", "ag7jc-3", "KO0OOO", "W1AW-15"):
            self.assertTrue(valid_call(c), c)
        for c in ("", "AB", "ABCDEF", "123456", "K1ABC-123", "K1/ABC", "K1ABC-", "K1 ABC", "ABCDEFGHI1J"):
            self.assertFalse(valid_call(c), c)

    def test_compose(self):
        self.assertEqual(compose_message(" hi ", "Pat", "fn42"), "hi\n\n-- Pat, FN42")
        self.assertEqual(compose_message("hi"), "hi")

    def test_reply_body(self):
        self.assertEqual(reply_body(b"type: reply\ncallsign: K1ABC\n\nThanks!\n"), "Thanks!")
        self.assertEqual(reply_body(b"just text\n\nmore"), "just text\n\nmore")

    def test_echo_summary(self):
        self.assertEqual(echo_summary("ECHO X de Y\nYour signal here: SNR +6.2 dB, speed 1400 bps\n"),
                         "SNR +6.2 dB, speed 1400 bps")

    def test_frame_matches_connector_format(self):
        self.assertEqual(frame("echo.txt", b"hi"), b"\x0b\x00\x00\x00echo.txt\nhi")


if __name__ == "__main__":
    unittest.main()


class LiveStatus(unittest.TestCase):
    def test_describe(self):
        from hf_contact.status import describe
        base = {"status": "listening", "busy": False, "freq_mhz": 14.105, "mode": "Mercury",
                "updated": "2026-10-29T11:40:00Z", "echoes": []}
        t = 1793274000   # 2026-10-29T11:40:00Z
        self.assertEqual(describe(base, now=t + 60, call="AG7JC"), ("listening", "AG7JC is listening now on 14.105 MHz"))
        self.assertEqual(describe(dict(base, busy=True), now=t + 60)[0], "busy")
        self.assertEqual(describe(dict(base, status="off", freq_mhz=None), now=t + 60)[0], "off")
        self.assertEqual(describe(base, now=t + 3 * 3600, call="AG7JC"),
                         ("off", "AG7JC hasn't checked in since 3 h ago -- probably off the air"))
        self.assertEqual(describe(None)[0], "unknown")
        self.assertEqual(describe({"updated": "yesterday"})[0], "unknown")

    def test_fetch_failure_is_none(self):
        from hf_contact.status import fetch
        self.assertIsNone(fetch("http://127.0.0.1:1/hf-contact.json", timeout=1))
        self.assertIsNone(fetch(""))


class SlowLinkWait(unittest.TestCase):
    """Check for replies on a slow link: the station's reply can take a
    minute to arrive, and hanging up after idle_after cut it off (first
    on-air test, 68 bps)."""

    def run_fake(self, bps, reply_after):
        import queue, threading
        from hf_contact import session

        class FakeTnc:
            def __init__(self, host, port):
                self.events, self.version, self.connected = queue.Queue(), "fake", False
            def open(self, mycall): pass
            def call(self, mycall, target):
                self.connected = True
                self.events.put(("connected", target))
                self.events.put(("bitrate", bps))
                threading.Timer(reply_after, lambda: self.events.put(
                    ("message", ("reply_1.txt", b"type: reply\n\nGot it, 73")))).start()
            def send(self, name, data): pass
            def hang_up(self): self.connected = False
            def close(self): pass

        real = session.MercuryTnc
        session.MercuryTnc = FakeTnc
        try:
            return session.run_session({"mycall": "K1ABC", "target": "AG7JC"}, idle_after=0.5)
        finally:
            session.MercuryTnc = real

    def test_slow_link_waits_for_the_reply(self):
        r = self.run_fake(bps=68, reply_after=1.2)
        self.assertEqual([b for _n, b in r.replies], ["Got it, 73"])

    def test_fast_link_keeps_the_short_wait(self):
        r = self.run_fake(bps=2000, reply_after=1.2)
        self.assertEqual(r.replies, [])
