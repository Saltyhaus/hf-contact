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
        base = {"callsign": "AG7JC", "status": "listening", "freq_hz": 14105000, "updated_unix": 1000,
                "stale_after": 300}
        self.assertEqual(describe(base, now=1060), ("listening", "AG7JC is listening now on 14.105 MHz"))
        self.assertEqual(describe(dict(base, status="busy"), now=1060)[0], "busy")
        self.assertEqual(describe(base, now=1000 + 7200), ("off", "AG7JC is off the air (last update 2 h ago)"))
        self.assertEqual(describe(dict(base, status="off", freq_hz=None), now=1060)[0], "off")
        self.assertEqual(describe(None)[0], "unknown")
        self.assertEqual(describe({"updated_unix": "x"})[0], "unknown")

    def test_fetch_failure_is_none(self):
        from hf_contact.status import fetch
        self.assertIsNone(fetch("http://127.0.0.1:1/station.json", timeout=1))
        self.assertIsNone(fetch(""))
