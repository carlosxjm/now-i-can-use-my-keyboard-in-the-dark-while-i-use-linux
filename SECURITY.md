# Security

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: *Security → Report a vulnerability* on the repository page. Don't open a public issue. This is a hobby project maintained in spare time, but security reports get priority.

## Privilege model

| Component | Runs as | Why |
|---|---|---|
| Settings window, tray icon/daemon, `avell-kbd --toggle` | your user | No root needed at runtime |
| `install-driver.sh`, `uninstall.sh --driver`, the `.deb` maintainer scripts | root | Building and installing a kernel module (DKMS) and a udev rule |
| `install.sh`, `uninstall.sh` | your user (refuses root) | Install into `~/.local` only |

## What the system changes allow

The **udev rule** (`99-my-keyboard-app.rules`) makes two sysfs attributes of the keyboard backlight writable by every local user (mode `0666`):

- `brightness`
- `multi_intensity` (the color)

It only applies to LED devices whose driver is `tuxedo_keyboard`, so keyboard LEDs from other drivers are not affected. These attributes only control the backlight; they don't give access to keystrokes or to anything else.

The **trade-off**: any local account (and any process running as one) can change the keyboard backlight. That matches common practice for keyboard backlights on single-user laptops. On a shared machine where that matters, edit the rule to use a group instead, e.g. `RUN+="/bin/chgrp kbdlight /sys%p/brightness"` plus `chmod 0664`, and add the allowed users to that group.

The **kernel module** comes from [tuxedo-drivers](https://github.com/tuxedocomputers/tuxedo-drivers), pinned to a reviewed commit in `packaging/tuxedo-drivers.ref`. Builds fetch exactly that commit. Only the Clevo keyboard modules are built (`packaging/driver/Kbuild`).

## Hardening in the code

- **The config file is untrusted input.** Every value is type-checked and clamped before use (`keyboard_rgb/config.py`), and a corrupt file falls back to defaults (a copy is kept as `config.json.corrupt`).
- **sysfs writes** are only ever integers produced by the app, clamped to the device's range, written to a fixed set of attributes of detected devices. Paths never come from the config.
- **No shell**: subprocesses are started with argument lists. The GNOME shortcut command is built from the launcher's own path with `shlex.quote`.
- **D-Bus**: the tray only exports the StatusNotifierItem/DBusMenu interfaces on the *session* bus, so only your own session can reach it. Its methods only change lighting settings. Invalid arguments are rejected with D-Bus errors, and exceptions are caught so a bad call can't stop the daemon.
- **Temporary files**: the config and status files are written atomically with `mkstemp` in their own directories (`~/.config/avell-kbd`, `$XDG_RUNTIME_DIR/avell-kbd` with mode 0700); nothing goes through shared `/tmp`.
