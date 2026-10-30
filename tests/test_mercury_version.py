import os
import sys
import tempfile
import unittest
from unittest import mock

from hf_contact import mercury_version as mv


def fake(path, version=None):
    """A stand-in Mercury: answers -V like 1.9.21, or prints usage like 1.9.13."""
    with open(path, "w") as f:
        if version:
            f.write(f'#!/bin/sh\n[ "$1" = -V ] && printf "\\033[0;31mRhizomatica Mercury Version {version} '
                    f'(git edca701b)\\033[0m\\n" && exit 0\nsleep 30\n')
        else:
            f.write('#!/bin/sh\necho "Usage: mercury [-h] [-c cpu_nr] ..." >&2\nexit 255\n')
    os.chmod(path, 0o755)
    return path


@unittest.skipIf(sys.platform == "win32", "uses a fake /proc")
class Local(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.proc = os.path.join(self.d, "proc")
        os.makedirs(self.proc)

    def run_as(self, pid, name, exe):
        p = os.path.join(self.proc, str(pid))
        os.makedirs(p)
        with open(os.path.join(p, "comm"), "w") as f:
            f.write(name + "\n")
        os.symlink(exe, os.path.join(p, "exe"))

    def test_running_new_mercury(self):
        self.run_as(100, "mercury", fake(os.path.join(self.d, "mercury"), "1.9.21"))
        self.run_as(101, "bash", "/bin/sh")
        self.assertEqual(mv.local_version(self.proc), ("ok", "1.9.21"))

    def test_running_old_mercury(self):
        self.run_as(100, "mercury", fake(os.path.join(self.d, "mercury")))
        self.assertEqual(mv.local_version(self.proc), ("old", None))

    def test_gui_asks_the_modem_next_to_it(self):
        fake(os.path.join(self.d, "mercury"), "1.9.21")
        self.run_as(100, "mercury-ui", fake(os.path.join(self.d, "mercury-ui")))
        self.assertEqual(mv.local_version(self.proc), ("ok", "1.9.21"))

    def test_nothing_running(self):
        self.assertEqual(mv.local_version(self.proc), ("not-running", None))
        self.assertEqual(mv.local_version(os.path.join(self.d, "nope")), ("not-running", None))

    def test_mercury_connector_is_not_mercury(self):
        self.run_as(100, "mercury-connect", "/bin/sh")       # comm is cut to 15 characters
        self.assertEqual(mv.local_version(self.proc), ("not-running", None))


class Windows(unittest.TestCase):
    def test_finds_exe_paths_from_powershell(self):
        out = "C:\\Program Files\\Mercury\\mercury-ui.exe\r\n\r\n"
        with mock.patch.object(mv.sys, "platform", "win32"), mock.patch.object(mv, "_run", return_value=out):
            self.assertEqual(mv.running_mercury(), ["C:\\Program Files\\Mercury\\mercury-ui.exe"])
        with mock.patch.object(mv.sys, "platform", "win32"), mock.patch.object(mv, "_run", return_value=None):
            self.assertEqual(mv.running_mercury(), [])


class Compare(unittest.TestCase):
    def test_station_version_is_checked(self):
        self.assertEqual(mv.station_version({"mercury_version": "1.9.21"}), "1.9.21")
        for bad in ({"mercury_version": "1.9.21\n"}, {"mercury_version": "<b>"}, {"mercury_version": None},
                    {}, None, "x"):
            self.assertIsNone(mv.station_version(bad), repr(bad))

    def test_messages(self):
        self.assertIsNone(mv.compare(("ok", "1.9.21"), None))                     # no internet: say nothing
        self.assertEqual(mv.compare(("ok", "1.9.21"), "1.9.21", "AG7JC"),
                         ("match", "Mercury 1.9.21 \u2713 same as AG7JC"))
        kind, text = mv.compare(("ok", "1.9.13"), "1.9.21", "AG7JC")
        self.assertEqual(kind, "mismatch")
        self.assertIn("You have Mercury 1.9.13; AG7JC runs 1.9.21", text)
        self.assertEqual(mv.compare(("old", None), "1.9.21", "AG7JC")[0], "mismatch")
        self.assertEqual(mv.compare(("ok", "1.9.22"), "1.9.21", "AG7JC")[0], "mismatch")   # newer is also different
        self.assertEqual(mv.compare(("not-running", None), "1.9.21", "AG7JC")[0], "info")
        self.assertEqual(mv.compare(("unknown", None), "1.9.21", "AG7JC")[0], "info")


if __name__ == "__main__":
    unittest.main()
