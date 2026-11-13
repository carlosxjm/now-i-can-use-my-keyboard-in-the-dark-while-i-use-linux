#!/usr/bin/env bash
# Installs Keyboard RGB for the current user, without root:
#
#   ~/.local/lib/my-keyboard-app/keyboard_rgb/              the app
#   ~/.local/bin/avell-kbd                                  launcher
#   ~/.local/share/applications/io.github.carlosxjm.KeyboardRGB.desktop
#   ~/.config/systemd/user/avell-kbd.service                tray icon / effects service
#
# Safe to run again (upgrades in place). The keyboard driver is installed
# separately: sudo ./install-driver.sh. Remove with ./uninstall.sh.
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run this without sudo: it installs into your home directory." >&2
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$HOME/.local"
LIB="$PREFIX/lib/my-keyboard-app"
BIN="$PREFIX/bin"
APPS="$PREFIX/share/applications"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_ID="io.github.carlosxjm.KeyboardRGB"

echo "==> Checking dependencies"
if ! /usr/bin/python3 - <<'EOF' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4)
EOF
then
  echo "Missing Python GTK4 (>= 4.10) / libadwaita (>= 1.4) bindings. Install them with:" >&2
  echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
  echo "(Ubuntu 24.04 or newer; Ubuntu 22.04's libadwaita is too old.)" >&2
  exit 1
fi

echo "==> Installing the app into $LIB"
mkdir -p "$PREFIX/lib" "$BIN" "$APPS" "$UNITS"
STAGE="$(mktemp -d "$PREFIX/lib/.my-keyboard-app.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -r "$DIR/keyboard_rgb" "$STAGE/"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$STAGE"
if [[ -d "$LIB" ]]; then
  rm -rf "$LIB.old"
  mv "$LIB" "$LIB.old"
fi
mv "$STAGE" "$LIB"
rm -rf "$LIB.old"
install -m 755 "$DIR/bin/avell-kbd" "$BIN/avell-kbd"

echo "==> App menu entry and user service"
sed "s|^Exec=.*|Exec=$BIN/avell-kbd|" "$DIR/packaging/files/$APP_ID.desktop" > "$APPS/$APP_ID.desktop"
rm -f "$APPS/vet.local.AvellKbd.desktop"  # name used by earlier versions
sed "s|^ExecStart=.*|ExecStart=$BIN/avell-kbd --daemon|" "$DIR/packaging/files/avell-kbd.service" \
  > "$UNITS/avell-kbd.service"
update-desktop-database "$APPS" 2>/dev/null || true

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user reenable avell-kbd.service >/dev/null 2>&1  # also drops links from older installs
  systemctl --user restart avell-kbd.service
  sleep 1  # let it start before the status check below
else
  echo "!! No systemd user session found: start it later with 'systemctl --user enable --now avell-kbd'"
fi

echo
echo "✔ Installed. The keyboard icon is in the top bar (click for the menu, scroll for brightness)."
if ! "$BIN/avell-kbd" --status; then
  echo
  echo "The keyboard driver isn't working yet. Install it with: sudo ./install-driver.sh"
fi
