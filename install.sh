#!/usr/bin/env bash
# Installs the "Keyboard RGB" app for the current user (no sudo):
# app menu entry + user service (tray icon, effects, restore colors on login).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
UNITS="$HOME/.config/systemd/user"

mkdir -p "$BIN" "$APPS" "$UNITS"
install -m 755 "$DIR/avell_kbd.py" "$BIN/avell-kbd"

cat > "$APPS/vet.local.AvellKbd.desktop" <<EOF
[Desktop Entry]
Name=Keyboard RGB
Comment=Keyboard backlight colors and effects
Exec=$BIN/avell-kbd
Icon=input-keyboard
Terminal=false
Type=Application
Categories=Settings;HardwareSettings;
Keywords=keyboard;rgb;backlight;teclado;luz;avell;
StartupWMClass=vet.local.AvellKbd
EOF

cat > "$UNITS/avell-kbd.service" <<EOF
[Unit]
Description=Keyboard RGB (tray icon, effects and color restore)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $BIN/avell-kbd --daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user disable avell-kbd.service 2>/dev/null || true  # clean up older installs (default.target)
systemctl --user enable --now avell-kbd.service
systemctl --user restart avell-kbd.service
update-desktop-database "$APPS" 2>/dev/null || true

echo "✔ Installed. The keyboard icon is in the top bar (click for the menu, scroll for brightness)."
"$BIN/avell-kbd" --status || true
