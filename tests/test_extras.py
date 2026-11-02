"""0.5.0 helpers: ADIF export, rigctld dial, station list, notifications."""
import os
import socket
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

from hf_contact import adif, notify, rig, stations


class Adif(unittest.TestCase):
    def test_band(self):
        self.assertEqual(adif.band_for(14.105), "20m")
        self.assertEqual(adif.band_for(7.1), "40m")
        self.assertIsNone(adif.band_for(None))
        self.assertIsNone(adif.band_for(13.0))

    def test_export(self):
        q = adif.qso("ag7jc", "k1abc", utc=datetime(2026, 11, 2, 16, 5, 9, tzinfo=timezone.utc), mhz=14.105,
                     grid="fn42", comment="echo test, they heard me SNR +4.0 dB <nice>")
        hist = [{"when": "x", "kind": "echo", "text": "...", "qso": q},
                {"when": "x", "kind": "reply", "text": "no contact here"},
                {"when": "old", "kind": "echo", "text": "from 0.4"}]
        text, n = adif.export(hist, now=datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(n, 1)
        self.assertIn("<EOH>", text)
        rec = text.split("<EOH>\n")[1]
        self.assertEqual(rec, "<CALL:5>AG7JC <QSO_DATE:8>20261102 <TIME_ON:6>160509 <MODE:7>DYNAMIC "
                              "<BAND:3>20m <FREQ:9>14.105000 <STATION_CALLSIGN:5>K1ABC <MY_GRIDSQUARE:4>FN42 "
                              "<COMMENT:52>Mercury: echo test, they heard me SNR +4.0 dB (nice) <EOR>\n")

    def test_lengths_count_bytes(self):
        q = adif.qso("K1ABC", "N0CALL", comment="73 · ok")
        rec = adif.record(q)
        self.assertIn("<COMMENT:17>Mercury: 73 · ok", rec)    # "·" is 2 bytes in UTF-8


class Rig(unittest.TestCase):
    def fake_rigctld(self, answer):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def run():
            c, _ = srv.accept()
            c.recv(16)
            c.sendall(answer)
            c.close()
            srv.close()
        threading.Thread(target=run, daemon=True).start()
        return f"127.0.0.1:{srv.getsockname()[1]}"

    def test_reads_dial(self):
        self.assertEqual(rig.read_mhz(self.fake_rigctld(b"14105000\n")), 14.105)

    def test_radio_not_answering(self):
        self.assertIsNone(rig.read_mhz(self.fake_rigctld(b"RPRT -5\n")))

    def test_nothing_there(self):
        self.assertIsNone(rig.read_mhz("127.0.0.1:1", timeout=1))
        self.assertIsNone(rig.read_mhz(""))

    def test_parse_addr(self):
        self.assertEqual(rig.parse_addr("4532"), ("127.0.0.1", 4532))
        self.assertEqual(rig.parse_addr("localhost"), ("localhost", 4532))
        self.assertEqual(rig.parse_addr(":4532"), ("127.0.0.1", 4532))
        self.assertEqual(rig.parse_addr("192.0.2.5:4534"), ("192.0.2.5", 4534))


class Stations(unittest.TestCase):
    def test_parse(self):
        doc = {"stations": [
            {"call": "ag7jc", "freq_mhz": 14.105, "grid": "dm12", "when": "Most  evenings", "echo_service": True,
             "status_url": "https://ag7jc.com/data/hf-contact.json"},
            {"call": "AG7JC"},                                   # duplicate
            {"call": "not a call"}, "junk", {"freq_mhz": 7.1},
            {"call": "K1ABC", "freq_mhz": "fast", "grid": "XX99", "status_url": "http://insecure"},
        ]}
        got = stations.parse(doc)
        self.assertEqual(got, [
            {"call": "AG7JC", "freq_mhz": 14.105, "grid": "DM12", "when": "Most evenings", "echo_service": True,
             "status_url": "https://ag7jc.com/data/hf-contact.json"},
            {"call": "K1ABC"}])
        self.assertEqual(stations.label(got[0]), "AG7JC · 14.105 MHz · DM12 · echo service")
        self.assertEqual(stations.as_settings(got[0]), {
            "call": "AG7JC", "freq": "14.105 MHz USB (dial)", "note": "DM12 · Most evenings · echo service",
            "status_url": "https://ag7jc.com/data/hf-contact.json"})
        self.assertEqual(stations.parse(None), [])
        self.assertEqual(stations.fetch("http://127.0.0.1:1/stations.json", timeout=1), [])


class Notify(unittest.TestCase):
    def test_linux_notify_send(self):
        d = tempfile.mkdtemp()
        log = os.path.join(d, "args")
        exe = os.path.join(d, "notify-send")
        with open(exe, "w") as f:
            f.write(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\n')
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        with mock.patch.dict(os.environ, {"PATH": d}), mock.patch("sys.platform", "linux"):
            self.assertTrue(notify.notify("Title", "K1ABC tested", icon="/x/icon.png"))
        for _ in range(50):
            if os.path.exists(log) and os.path.getsize(log):
                break
            threading.Event().wait(0.05)
        self.assertEqual(open(log).read().splitlines(),
                         ["--app-name=HF Contact", "--icon=/x/icon.png", "Title", "K1ABC tested"])

    def test_no_notifier(self):
        with mock.patch.dict(os.environ, {"PATH": tempfile.mkdtemp()}), mock.patch("sys.platform", "linux"):
            self.assertFalse(notify.notify("a", "b"))

    def test_windows_quotes(self):
        self.assertEqual(notify._ps_quote("it's"), "'it''s'")


if __name__ == "__main__":
    unittest.main()
