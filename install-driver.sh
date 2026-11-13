#!/usr/bin/env bash
# Installs the keyboard driver (tuxedo-drivers, Clevo modules only) via DKMS
# plus a udev rule that lets regular users control the keyboard.
# (Not needed if you installed the .deb package: it already does all of this.)
#
# Usage: sudo ./install-driver.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="https://github.com/tuxedocomputers/tuxedo-drivers.git"
REF="$(tr -d '[:space:]' < "$DIR/packaging/tuxedo-drivers.ref")"
NAME="my-keyboard-app"
VER="src.${REF:0:7}"
SRC="/usr/src/$NAME-$VER"
WORK="$(mktemp -d)"
ADDED=""
# shellcheck disable=SC2317  # only called through the EXIT trap
cleanup() {
  rm -rf "$WORK"
  # A failed build must not leave a half-registered driver behind
  if [[ -n "$ADDED" ]]; then
    echo "==> Rolling back $NAME/$VER" >&2
    dkms remove -m "$NAME" -v "$VER" --all >/dev/null 2>&1 || true
    rm -rf "$SRC"
  fi
}
trap cleanup EXIT

if ! command -v apt-get >/dev/null; then
  echo "This script supports Debian/Ubuntu (apt). On other distributions install dkms," >&2
  echo "your kernel headers and git, then follow the manual steps in the README." >&2
  exit 1
fi

echo "==> Installing dependencies (build-essential, dkms, git)"
apt-get update -qq
apt-get install -y build-essential dkms git "linux-headers-$(uname -r)"

echo "==> Downloading tuxedo-drivers@${REF:0:7}"
git init -q "$WORK/tuxedo"
git -C "$WORK/tuxedo" fetch -q --depth 1 "$REPO" "$REF"
git -C "$WORK/tuxedo" checkout -q FETCH_HEAD

installed_versions() {  # versions of DKMS module $1 (any state)
  dkms status -m "$1" 2>/dev/null | sed -n 's|^[^/]*/\([^,:]*\).*|\1|p' | sort -u
}

if dkms status -m "$NAME" -v "$VER" -k "$(uname -r)" 2>/dev/null | grep -q ": installed"; then
  echo "==> $NAME/$VER is already installed for $(uname -r)"
else
  # Build the new version first; older versions stay untouched until it succeeds
  echo "==> Copying sources to $SRC"
  dkms remove -m "$NAME" -v "$VER" --all >/dev/null 2>&1 || true  # leftover of an interrupted run
  rm -rf "$SRC"
  ADDED=1
  mkdir -p "$SRC"
  cp -r "$WORK/tuxedo/src/." "$SRC/"
  cp "$DIR/packaging/driver/Kbuild" "$SRC/Kbuild"
  sed "s|@NAME@|$NAME|; s|@VERSION@|$VER|" "$DIR/packaging/driver/dkms.conf.in" > "$SRC/dkms.conf"

  echo "==> Building with DKMS (may take a minute)"
  dkms add -m "$NAME" -v "$VER"
  if ! dkms build -m "$NAME" -v "$VER"; then
    echo "!! Build failed; your current driver (if any) was left as it was. Log:" >&2
    cat "/var/lib/dkms/$NAME/$VER/build/make.log" >&2 || true
    exit 1
  fi

  # Now replace older versions (including the earlier "avell-clevo-kbd" name)
  for mod in avell-clevo-kbd "$NAME"; do
    for old in $(installed_versions "$mod"); do
      [[ "$mod/$old" == "$NAME/$VER" ]] && continue
      echo "==> Removing previous driver $mod/$old"
      dkms remove -m "$mod" -v "$old" --all || true
      rm -rf "/usr/src/$mod-$old"
    done
  done

  echo "==> Installing with DKMS"
  dkms install -m "$NAME" -v "$VER"
  ADDED=""  # installed: keep it
fi
rm -f /etc/udev/rules.d/99-avell-kbd.rules /etc/modules-load.d/avell-kbd.conf

echo "==> udev rule (control without root)"
install -m644 "$DIR/packaging/files/99-my-keyboard-app.rules" /etc/udev/rules.d/99-my-keyboard-app.rules
udevadm control --reload-rules
echo "clevo_acpi" > /etc/modules-load.d/my-keyboard-app.conf

echo "==> Loading modules"
modprobe -r clevo_acpi clevo_wmi tuxedo_keyboard 2>/dev/null || true
modprobe clevo_acpi || modprobe clevo_wmi
sleep 1
udevadm trigger --subsystem-match=leds --action=add

echo
if compgen -G '/sys/class/leds/*kbd_backlight*' >/dev/null; then
  echo "✔ Keyboard detected:"
  ls -d /sys/class/leds/*kbd_backlight*
  echo
  echo "Now run (without sudo): ./install.sh"
else
  echo "!! Modules loaded, but no kbd_backlight device showed up. Check: sudo dmesg | grep -iE 'clevo|tuxedo'"
  exit 1
fi
