#!/usr/bin/env bash
# Removes a manual install (install.sh / install-driver.sh).
# With --driver (and sudo) also removes the driver and udev rule.
# If you installed the .deb package, use: sudo apt remove my-keyboard-app
set -uo pipefail

if [[ "${1:-}" == "--driver" ]]; then
  [[ $EUID -eq 0 ]] || { echo "To remove the driver: sudo $0 --driver" >&2; exit 1; }
  if dpkg-query -W -f='${Status}' my-keyboard-app 2>/dev/null | grep -q "install ok installed"; then
    echo "The driver belongs to the my-keyboard-app package. Use: sudo apt remove my-keyboard-app" >&2
    exit 1
  fi
  for mod in avell-clevo-kbd my-keyboard-app; do
    for v in $(dkms status -m "$mod" 2>/dev/null | sed -n 's|^[^/]*/\([^,:]*\).*|\1|p' | sort -u); do
      dkms remove -m "$mod" -v "$v" --all
      rm -rf "/usr/src/$mod-$v"
    done
  done
  rm -f /etc/udev/rules.d/99-avell-kbd.rules /etc/modules-load.d/avell-kbd.conf \
        /etc/udev/rules.d/99-my-keyboard-app.rules /etc/modules-load.d/my-keyboard-app.conf
  udevadm control --reload-rules
  echo "✔ Driver removed (reboot to unload it)."
  exit 0
fi

systemctl --user disable --now avell-kbd.service 2>/dev/null
"$HOME/.local/bin/avell-kbd" --remove-shortcut 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/avell-kbd.service" \
      "$HOME/.local/bin/avell-kbd" \
      "$HOME/.local/share/applications/vet.local.AvellKbd.desktop"
systemctl --user daemon-reload
echo "✔ App removed. (Config kept in ~/.config/avell-kbd)"
echo "  To remove the driver too: sudo $0 --driver"
