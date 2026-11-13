# Keyboard RGB — Avell / Clevo keyboard backlight on Linux

A Linux replacement for the Windows Control Center: control the RGB keyboard backlight of Clevo-based laptops (such as the Avell A57).

- **Tray icon** in the top bar, drawn in the keyboard's current color:
  - click: quick menu (on/off, effect, color, brightness, settings)
  - scroll over the icon: adjust brightness
  - middle click: toggle on/off
- **Global shortcut** to toggle the light: **Ctrl+Super+K** by default (shown next to "On" in the tray menu). It's a regular GNOME custom shortcut, so you can change or delete it in Settings → Keyboard → Keyboard Shortcuts.
- Full settings window (live preview, custom color, speed, zones)
- Whole-keyboard or per-zone color (3-zone keyboards)
- Effects: Static, Breathing, Color cycle, Rainbow wave and Flash, with adjustable speed
- Restores your colors on login and after suspend
- Follows brightness changes made with the Fn keys

## Install

### From the .deb package (recommended)

Download `my-keyboard-app_*_all.deb` from the [latest release](../../releases/latest) and run:

```bash
sudo apt install ./my-keyboard-app_*_all.deb
```

apt pulls in the dependencies, builds the driver with DKMS and installs everything. Log out and back in to get the tray icon. To remove: `sudo apt remove my-keyboard-app`.

If you used the manual install before, run `./uninstall.sh` first so your old user service doesn't shadow the packaged one.

### Manual install (from source)

```bash
sudo ./install-driver.sh   # once: driver (DKMS) + udev permission
./install.sh               # app + user service (no sudo)
```

The icon shows up in the top bar. Open the settings window from the icon's menu or from **Keyboard RGB** in the app grid.

On GNOME the icon needs the AppIndicator extension, which Ubuntu ships enabled.

CLI:
- `avell-kbd --toggle` turns the light on/off.
- `avell-kbd --status` shows the detected hardware.
- `journalctl --user -u avell-kbd` shows the service log.

## How it works

| Layer | What it does |
|---|---|
| `tuxedo-drivers` (modules `clevo_acpi`, `tuxedo_keyboard`) | Talks to the Clevo firmware and exposes `/sys/class/leds/rgb:kbd_backlight*` |
| `/etc/udev/rules.d/99-avell-kbd.rules` | Makes those files writable by your user |
| `avell-kbd --daemon` (systemd user service) | Tray icon (StatusNotifierItem over D-Bus); reads `~/.config/avell-kbd/config.json`, applies colors and animates effects |
| `avell-kbd` (GTK4/libadwaita) | Settings window, which edits the same config file |

The driver is installed via DKMS, so it is rebuilt automatically on kernel updates.

## Uninstall (manual install)

```bash
./uninstall.sh                # remove the app
sudo ./uninstall.sh --driver  # remove the driver and udev rule
```

## Building the package

```bash
./packaging/build-deb.sh      # -> dist/my-keyboard-app_<version>_all.deb
```

GitHub Actions ([.github/workflows/build-deb.yml](.github/workflows/build-deb.yml)) runs this on every push and pull request. It also test-installs the package, which builds the driver with DKMS, and uploads the `.deb` as a workflow artifact. Pushing a `vX.Y.Z` tag also publishes a GitHub release with the `.deb` attached:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

The driver version is pinned in [packaging/tuxedo-drivers.ref](packaging/tuxedo-drivers.ref) (a tuxedo-drivers commit).
