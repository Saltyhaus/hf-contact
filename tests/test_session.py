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
