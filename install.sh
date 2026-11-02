#!/bin/sh
# Add HF Contact to the app grid / menu (Linux), with its icon.
# Run it from the folder you cloned. Undo: ./install.sh --remove
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

if [ "$1" = "--remove" ]; then
    rm -f "$APPS/hf-contact.desktop" "$ICONS/hf-contact.png"
    echo "Removed HF Contact from the menu."
    exit 0
fi

python3 -c "import tkinter" 2>/dev/null || {
    echo "Python's Tk is missing. On Debian/Ubuntu: sudo apt install python3-tk"; exit 1; }

mkdir -p "$APPS" "$ICONS"
cp "$HERE/assets/icon.png" "$ICONS/hf-contact.png"
# Icon is the PNG's full path: an icon name only shows up after GNOME rescans
# its icon folders (often not until the next login).
# StartupWMClass matches the window (Tk className "hf-contact"), so the dock
# groups it under this entry and shows the icon
cat > "$APPS/hf-contact.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=HF Contact
Comment=Echo test and short messages over HF, through Mercury
Exec=python3 "$HERE/hf-contact.py"
Path=$HERE
Icon=$HERE/assets/icon.png
Terminal=false
Categories=HamRadio;Network;
StartupWMClass=Hf-contact
DESKTOP
chmod +x "$HERE/hf-contact.py"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -q "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
echo "HF Contact is in your app menu. You can still run ./hf-contact.py directly."
