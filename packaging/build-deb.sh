#!/usr/bin/env bash
# Builds dist/my-keyboard-app_<version>_all.deb
#
# Version:
#   - on a tag vX.Y.Z           -> X.Y.Z
#   - N commits after tag vX.Y.Z -> X.Y.Z+gitN.<sha>
#   - no tags yet               -> <VERSION file>~gitN.<sha>
#   - override with VERSION=... ./packaging/build-deb.sh
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="my-keyboard-app"
TUXEDO_REPO="https://github.com/tuxedocomputers/tuxedo-drivers.git"
TUXEDO_REF="$(tr -d '[:space:]' < "$ROOT/packaging/tuxedo-drivers.ref")"

# ---------------------------------------------------------------- version
if [[ -z "${VERSION:-}" ]]; then
  BASE="$(tr -d '[:space:]' < "$ROOT/VERSION")"
  if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    if DESC="$(git -C "$ROOT" describe --tags --match 'v[0-9]*' 2>/dev/null)"; then
      # v1.2.0 | v1.2.0-5-gabc1234
      VERSION="$(sed -E 's/^v//; s/-([0-9]+)-g([0-9a-f]+)$/+git\1.\2/' <<<"$DESC")"
    else
      VERSION="$BASE~git$(git -C "$ROOT" rev-list --count HEAD).$(git -C "$ROOT" rev-parse --short HEAD)"
    fi
  else
    VERSION="$BASE"
  fi
fi
# DKMS paths are happier without '~' and '+'
DKMS_VERSION="$(tr '~+' '..' <<<"$VERSION")"
MAINTAINER="${MAINTAINER:-${GITHUB_REPOSITORY_OWNER:-$NAME} <noreply@github.com>}"
HOMEPAGE="${HOMEPAGE:-https://github.com/${GITHUB_REPOSITORY:-$NAME/$NAME}}"

echo "==> Building $NAME $VERSION (driver: tuxedo-drivers@${TUXEDO_REF:0:7})"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PKG="$WORK/pkg"

# ---------------------------------------------------------------- driver sources
git init -q "$WORK/tuxedo"
git -C "$WORK/tuxedo" fetch -q --depth 1 "$TUXEDO_REPO" "$TUXEDO_REF"
git -C "$WORK/tuxedo" checkout -q FETCH_HEAD

SRC="$PKG/usr/src/$NAME-$DKMS_VERSION"
mkdir -p "$SRC"
cp -r "$WORK/tuxedo/src/." "$SRC/"
cp "$ROOT/packaging/driver/Kbuild" "$SRC/Kbuild"
sed "s|@NAME@|$NAME|; s|@VERSION@|$DKMS_VERSION|" "$ROOT/packaging/driver/dkms.conf.in" > "$SRC/dkms.conf"

# ---------------------------------------------------------------- app files
install -Dm755 "$ROOT/avell_kbd.py"                          "$PKG/usr/bin/avell-kbd"
install -Dm644 "$ROOT/packaging/files/my-keyboard-app.desktop" "$PKG/usr/share/applications/$NAME.desktop"
install -Dm644 "$ROOT/packaging/files/avell-kbd.service"     "$PKG/usr/lib/systemd/user/avell-kbd.service"
install -Dm644 "$ROOT/packaging/files/99-my-keyboard-app.rules" "$PKG/usr/lib/udev/rules.d/99-$NAME.rules"
install -d "$PKG/usr/lib/modules-load.d"
echo "clevo_acpi" > "$PKG/usr/lib/modules-load.d/$NAME.conf"
# Enable the tray service for every user's graphical session
install -d "$PKG/usr/lib/systemd/user/graphical-session.target.wants"
ln -s ../avell-kbd.service "$PKG/usr/lib/systemd/user/graphical-session.target.wants/avell-kbd.service"

DOC="$PKG/usr/share/doc/$NAME"
install -Dm644 "$ROOT/README.md" "$DOC/README.md"
cat > "$DOC/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: $NAME
Source: $HOMEPAGE

Files: usr/src/$NAME-$DKMS_VERSION/*
Copyright: 2018-2025 TUXEDO Computers GmbH <tux@tuxedocomputers.com>
License: GPL-2.0-or-later
Comment: From $TUXEDO_REPO (commit $TUXEDO_REF)

License: GPL-2.0-or-later
 On Debian systems, the full text of the GNU General Public License version 2
 can be found in /usr/share/common-licenses/GPL-2.
EOF

# ---------------------------------------------------------------- control files
install -d "$PKG/DEBIAN"
for f in postinst prerm; do
  sed "s|@NAME@|$NAME|; s|@DKMS_VERSION@|$DKMS_VERSION|" "$ROOT/packaging/debian/$f" > "$PKG/DEBIAN/$f"
  chmod 755 "$PKG/DEBIAN/$f"
done

cat > "$PKG/DEBIAN/control" <<EOF
Package: $NAME
Version: $VERSION
Architecture: all
Maintainer: $MAINTAINER
Installed-Size: $(du -sk --exclude=DEBIAN "$PKG" | cut -f1)
Depends: dkms, kmod, udev, python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-graphene-1.0
Recommends: linux-headers-generic | linux-headers, gnome-shell-extension-appindicator
Conflicts: tuxedo-drivers, tuxedo-keyboard
Section: utils
Priority: optional
Homepage: $HOMEPAGE
Description: Keyboard RGB backlight control for Clevo-based laptops (Avell, etc.)
 Tray icon and settings window to control the RGB keyboard backlight of
 Clevo-based laptops: colors, per-zone colors, brightness and effects
 (breathing, color cycle, rainbow wave, flash).
 .
 Includes the Clevo keyboard modules from tuxedo-drivers, built with DKMS.
EOF

# ---------------------------------------------------------------- build
chmod -R u+rwX,go+rX,go-w "$PKG"
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/${NAME}_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT" >/dev/null
echo "==> $OUT"
