#!/usr/bin/env bash
# Removes a manual install (install.sh / install-driver.sh). Only files this
# project created are removed; your settings in ~/.config/avell-kbd are kept.
#
#   ./uninstall.sh                 remove the app (no sudo)
#   sudo ./uninstall.sh --driver   remove the driver and udev rule
#
# If you installed the .deb package, use: sudo apt remove my-keyboard-app
set -uo pipefail

NAME="my-keyboard-app"

if [[ "${1:-}" == "--driver" ]]; then
  [[ $EUID -eq 0 ]] || { echo "To remove the driver: sudo $0 --driver" >&2; exit 1; }
  if dpkg-query -W -f='${Status}' "$NAME" 2>/dev/null | grep -q "install ok installed"; then
    echo "The driver belongs to the $NAME package. Use: sudo apt remove $NAME" >&2
    exit 1
  fi
  for mod in avell-clevo-kbd "$NAME"; do
    for v in $(dkms status -m "$mod" 2>/dev/null | sed -n 's|^[^/]*/\([^,:]*\).*|\1|p' | sort -u); do
      echo "==> Removing driver $mod/$v"
      dkms remove -m "$mod" -v "$v" --all
      rm -rf "/usr/src/$mod-$v"
    done
  done
  rm -f /etc/udev/rules.d/99-avell-kbd.rules /etc/modules-load.d/avell-kbd.conf \
        "/etc/udev/rules.d/99-$NAME.rules" "/etc/modules-load.d/$NAME.conf"
  udevadm control --reload-rules
  echo "✔ Driver removed (reboot to unload it)."
  exit 0
fi

if [[ $EUID -eq 0 ]]; then
  echo "Run without sudo to remove the app from your account (or use --driver)." >&2
  exit 1
fi

PREFIX="$HOME/.local"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user disable --now avell-kbd.service 2>/dev/null
fi
# delete the GNOME shortcut while the app is still installed
[[ -x "$PREFIX/bin/avell-kbd" ]] && "$PREFIX/bin/avell-kbd" --remove-shortcut 2>/dev/null

rm -rf "$PREFIX/lib/$NAME"
rm -f "$PREFIX/bin/avell-kbd" \
      "$PREFIX/share/applications/io.github.carlosxjm.KeyboardRGB.desktop" \
      "$PREFIX/share/applications/vet.local.AvellKbd.desktop" \
      "$UNITS/avell-kbd.service"
if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
fi

echo "✔ App removed. (Settings kept in ~/.config/avell-kbd)"
echo "  To remove the driver too: sudo $0 --driver"
