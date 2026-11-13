# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

First public development release.

### Added
- Tray icon (StatusNotifierItem + DBusMenu) with a menu for on/off, effect, color, brightness and settings. Scroll over it to change brightness; middle-click to toggle.
- Global on/off shortcut (Ctrl+Super+K), registered as a GNOME custom shortcut and shown in the tray menu.
- GTK4/libadwaita settings window with a live preview, brightness, effects (Static, Breathing, Color cycle, Rainbow wave, Flash), speed, preset and custom colors, and per-zone colors on 3-zone keyboards.
- Background service that restores settings on login and after suspend, and adopts brightness changes made elsewhere.
- Status reporting: the window and the tray show when no supported keyboard is found, when permissions are missing, when the service isn't running, and when writes to the keyboard fail.
- `.deb` package (driver built with DKMS) and manual install scripts.
- Test suite with simulated hardware, including D-Bus and GUI smoke tests, and an opt-in real-hardware check (`tests/hardware/check_real_hardware.py`).
- CI: shellcheck, validation of the desktop entry and udev rule, tests, package build and test install.

### Security
- The udev rule only grants write access to keyboard LEDs created by the `tuxedo_keyboard` driver, and only to their `brightness` and `multi_intensity` attributes.
- No shell is involved in the udev rule. The app never runs as root and never builds shell commands from config values.

[0.1.0]: https://github.com/carlosxjm/now-i-can-use-my-keyboard-in-the-dark-while-i-use-linux/releases/tag/v0.1.0
