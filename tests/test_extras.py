"""0.5.0 helpers: ADIF export, rigctld dial, station list, notifications."""
import os
import socket
import stat
import tempfile
import threading
import unittest
from datetime import date, datetime, timezone
from unittest import mock

from hf_contact import adif, listing, notify, rig, stations, updates


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
    DOC = {"updated": "2026-11-03T18:00:00Z",
           "submit": {"call": "ag7jc", "email": "joshua@ag7jc.com"},
           "stations": [
               {"call": "ag7jc", "freq_mhz": 14.105, "grid": "dm12", "hours_utc": [1, 6], "when": "most  evenings",
                "echo_service": True, "worked": True, "last_confirmed": "2026-11-03",
                "status_url": "https://ag7jc.com/data/hf-contact.json"},
               {"call": "AG7JC"},                                   # duplicate
               {"call": "not a call"}, "junk", {"freq_mhz": 7.1},
               {"call": "VU2XYZ", "freq_mhz": "fast", "grid": "XX99", "hours_utc": [25, 3],
                "status_url": "http://insecure", "last_confirmed": "2026-01-02"}]}

    def test_parse(self):
        got = stations.parse(self.DOC)
        self.assertEqual(got["submit"], {"call": "AG7JC", "email": "joshua@ag7jc.com"})
        self.assertEqual(got["updated"], "2026-11-03T18:00:00Z")
        self.assertEqual(got["stations"], [
            {"call": "AG7JC", "freq_mhz": 14.105, "grid": "DM12", "hours_utc": [1, 6], "when": "most evenings",
             "echo_service": True, "worked": True, "last_confirmed": "2026-11-03",
             "status_url": "https://ag7jc.com/data/hf-contact.json"},
            {"call": "VU2XYZ", "last_confirmed": "2026-01-02"}])
        self.assertEqual(stations.parse(None), {"stations": [], "submit": None, "updated": None})
        self.assertIsNone(stations.parse({"submit": {"call": "x", "email": "nope"}})["submit"])

    def test_labels_and_settings(self):
        ag, vu = stations.parse(self.DOC)["stations"]
        today = date(2026, 11, 3)
        self.assertEqual(stations.label(ag, today), "AG7JC · 14.105 MHz · DM12 · echo service · worked on air")
        self.assertEqual(stations.label(vu, today), "VU2XYZ · listed · not confirmed since 2026-01")
        self.assertEqual(stations.hours_text(ag, offset_hours=-8), "01:00–06:00 UTC (17:00–22:00 your time)")
        self.assertEqual(stations.as_settings(ag, offset_hours=-8), {
            "call": "AG7JC", "freq": "14.105 MHz USB (dial)",
            "note": "DM12 · 01:00–06:00 UTC (17:00–22:00 your time) · echo service",
            "status_url": "https://ag7jc.com/data/hf-contact.json"})
        self.assertEqual(stations.as_settings(vu)["status_url"], "", "another station's status URL can't stay")

    def test_saved_copy_when_offline(self):
        import http.server
        import json as _json
        body = _json.dumps(self.DOC).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/stations.json"
        cache = os.path.join(tempfile.mkdtemp(), "stations-cache.json")
        fresh = stations.fetch_cached(url, cache)
        self.assertEqual(len(fresh["stations"]), 2)
        self.assertNotIn("cached_at", fresh)
        srv.shutdown()
        srv.server_close()
        offline = stations.fetch_cached(url, cache, timeout=1)
        self.assertEqual(offline["stations"], fresh["stations"])
        self.assertEqual(offline["submit"], fresh["submit"])
        self.assertTrue(offline["cached_at"])
        self.assertIsNone(stations.fetch_cached("http://127.0.0.1:1/other.json", cache, timeout=1),
                          "a saved copy only stands in for the same list")


class Listing(unittest.TestCase):
    def test_request_round_trip(self):
        text, problems = listing.build("add", "vu2xyz", "mk82qx", 14.105, (13, 16), True, "1.9.21",
                                       "Bangalore,  100 W\nand a dipole", True)
        self.assertEqual(problems, [])
        self.assertEqual(text, "STATION LIST REQUEST\naction: add\ncall: VU2XYZ\ngrid: MK82\nfreq: 14.105\n"
                               "hours_utc: 13-16\necho_service: yes\nmercury: 1.9.21\npublic: yes\n"
                               "note: Bangalore, 100 W and a dipole\n")
        self.assertEqual(listing.parse(text)["call"], "VU2XYZ")
        self.assertEqual(listing.parse(text)["hours_utc"], "13-16")
        self.assertIsNone(listing.parse("hello"))

    def test_problems(self):
        self.assertIsNone(listing.build("add", "", public=True)[0])
        text, problems = listing.build("add", "K1ABC", grid="12AB", public=False)
        self.assertIsNone(text)
        self.assertEqual(len(problems), 2)
        text, problems = listing.build("remove", "K1ABC", public=False)    # removing needs no consent box
        self.assertEqual(text, "STATION LIST REQUEST\naction: remove\ncall: K1ABC\n")

    def test_utc_and_mail(self):
        self.assertEqual(listing.utc_hours(18, 22, offset_hours=-8), (2, 6))
        self.assertEqual(listing.utc_hours(18, 22, offset_hours=5), (13, 17))
        url = listing.mailto("joshua@ag7jc.com", "K1ABC", "STATION LIST REQUEST\naction: add\n")
        self.assertTrue(url.startswith("mailto:joshua@ag7jc.com?subject=Station%20list%3A%20add%20K1ABC&body="))
        self.assertIn("STATION%20LIST%20REQUEST%0Aaction%3A%20add", url)


class Updates(unittest.TestCase):
    def test_newer(self):
        self.assertEqual(updates.newer("v0.5.1", "0.5.0"), "0.5.1")
        self.assertEqual(updates.newer("v0.10.0", "0.9.21"), "0.10.0")
        self.assertIsNone(updates.newer("v0.5.0", "0.5.0"))
        self.assertIsNone(updates.newer("v0.4.9", "0.5.0"))
        self.assertIsNone(updates.newer("nightly", "0.5.0"))

    def test_offline(self):
        self.assertIsNone(updates.check("0.5.0", url="http://127.0.0.1:1/latest", timeout=1))


class Reminder(unittest.TestCase):
    def test_reminder(self):
        from hf_contact.mercury_version import reminder
        self.assertEqual(reminder(("ok", "1.9.21"), "K1ABC"), "Your Mercury is 1.9.21. K1ABC needs 1.9.20 or newer to connect.")
        self.assertIn("older than 1.9.20", reminder(("ok", "1.9.13"), "K1ABC"))
        self.assertIn("compatible Mercury versions", reminder(("not-running", None), "K1ABC"))


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
