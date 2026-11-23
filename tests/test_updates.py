"""Update now: the zip swap, the git fast-forward, refusing a wrong download."""
import io
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from hf_contact import themes, updates


def release_zip(version, top="hf-contact-X"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(f"{top}/", "")
        z.writestr(f"{top}/hf_contact/__init__.py", f'__version__ = "{version}"\n')
        z.writestr(f"{top}/hf_contact/app.py", "# new\n")
        z.writestr(f"{top}/hf-contact.py", "# start\n")
        z.writestr(f"{top}/../evil.txt", "no")
    return b.getvalue()


class Updates(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "hf_contact").mkdir()
        (self.root / "hf_contact" / "__init__.py").write_text('__version__ = "0.5.2"\n')
        (self.root / "hf_contact" / "app.py").write_text("# old\n")

    def test_how(self):
        self.assertEqual(updates.how(self.root, frozen=False), "zip")
        self.assertEqual(updates.how(self.root, frozen=True), "exe")
        (self.root / ".git").mkdir()
        self.assertEqual(updates.how(self.root, frozen=False), "git")

    def test_zip_swap_keeps_the_old_one(self):
        orig = updates._get
        updates._get = lambda url, **k: release_zip("0.6.0")
        try:
            ok, msg = updates.install("0.6.0", root=self.root, mode="zip")
        finally:
            updates._get = orig
        self.assertTrue(ok, msg)
        self.assertEqual((self.root / "hf_contact" / "app.py").read_text(), "# new\n")
        self.assertEqual((self.root / "hf_contact.old" / "app.py").read_text(), "# old\n")
        self.assertFalse((self.root.parent / "evil.txt").exists())

    def test_wrong_download_changes_nothing(self):
        orig = updates._get
        updates._get = lambda url, **k: release_zip("0.5.9")
        try:
            ok, msg = updates.install("0.6.0", root=self.root, mode="zip")
        finally:
            updates._get = orig
        self.assertFalse(ok)
        self.assertEqual((self.root / "hf_contact" / "app.py").read_text(), "# old\n")
        self.assertFalse((self.root / "hf_contact.old").exists())
        self.assertFalse(updates.install("not-a-version", root=self.root, mode="zip")[0])

    def test_git_fast_forward(self):
        def git(*a, cwd):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                           capture_output=True)
        up = Path(tempfile.mkdtemp())
        git("init", "-q", "-b", "main", cwd=up)
        (up / "f").write_text("1")
        git("add", ".", cwd=up)
        git("commit", "-qm", "one", cwd=up)
        here = Path(tempfile.mkdtemp()) / "clone"
        subprocess.run(["git", "clone", "-q", str(up), str(here)], check=True)
        (up / "f").write_text("2")
        git("commit", "-qam", "two", cwd=up)
        ok, msg = updates.install("0.6.0", root=here, mode="git")
        self.assertTrue(ok, msg)
        self.assertEqual((here / "f").read_text(), "2")
        (here / "f").write_text("mine")
        git("commit", "-qam", "local", cwd=here)
        (up / "f").write_text("3")
        git("commit", "-qam", "three", cwd=up)
        ok, msg = updates.install("0.6.1", root=here, mode="git")
        self.assertFalse(ok)
        self.assertIn("its own changes", msg)


class Themes(unittest.TestCase):
    def test_every_theme_has_every_colour(self):
        keys = set(themes.THEMES["classic"]) - {"label", "TITLE_FONT"}
        for name, t in themes.THEMES.items():
            self.assertTrue(keys <= set(t), name)
        self.assertEqual(themes.get("nope")["label"], "AG7JC")
        self.assertEqual(themes.by_label("Night (red)"), "night")


if __name__ == "__main__":
    unittest.main()
