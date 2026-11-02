"""
A desktop notification, without extra packages: notify-send on Linux,
a toast through PowerShell on Windows. Best effort: nothing installed or
anything going wrong just means no pop-up.

No sound of our own on purpose. On a radio computer the default sound
card may well be the radio's, and an alert must never go out on the air.
"""
import shutil
import subprocess
import sys

_NO_WINDOW = 0x08000000
# PowerShell's own app id: the one id every Windows install lets show toasts
_PS_APP = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def notify(title, body, icon=None):
    """icon: path to a PNG (Linux only)."""
    try:
        if sys.platform == "win32":
            script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null;"
                "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$t = $x.GetElementsByTagName('text');"
                f"$t.Item(0).AppendChild($x.CreateTextNode({_ps_quote(title)})) > $null;"
                f"$t.Item(1).AppendChild($x.CreateTextNode({_ps_quote(body)})) > $null;"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                f"{_ps_quote(_PS_APP)}).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW)
            return True
        exe = shutil.which("notify-send")
        if not exe:
            return False
        subprocess.Popen([exe, "--app-name=HF Contact"] + ([f"--icon={icon}"] if icon else []) + [title, body],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        return False
