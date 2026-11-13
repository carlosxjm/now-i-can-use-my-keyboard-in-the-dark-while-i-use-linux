# Contributing

Thanks for helping people use their keyboards in the dark!

## Reports

- **Compatibility reports** are the most useful contribution right now, whether your laptop works or not. Run `./tests/hardware/check_real_hardware.py --yes --stop-service` and paste its output into a *Compatibility report* issue.
- **Bugs**: open a *Bug report* with the output of `avell-kbd --status` and `journalctl --user -u avell-kbd -b`.
- **Security problems**: see [SECURITY.md](SECURITY.md). Please don't open public issues for these.

## Code

1. Fork the repository and create a branch.
2. Make your change. Keep it focused, and preserve existing behavior unless the change is about that behavior.
3. Add or update tests. Hardware must be mocked (see `FakeLeds` in `tests/helpers.py`), so the suite runs anywhere.
4. Run `./tests/run.sh` and, if you have it installed, `shellcheck` on the shell scripts.
5. Add a line to the *Unreleased* section of `CHANGELOG.md`.
6. Open a pull request and fill in the template. Say what you tested on real hardware, if anything.

### Ground rules

- **No speculative hardware support.** A new laptop family gets its own backend class in `keyboard_rgb/hardware.py`, together with evidence that it works: a compatibility report, driver documentation, or an upstream driver.
- **The app never runs as root.** Anything that needs privileges belongs in the install scripts or the package, and must be documented in the README's "What gets installed" table.
- **No new runtime dependencies** beyond what Debian/Ubuntu ship (Python standard library, PyGObject, GTK4, libadwaita) without discussion.
- **Keep the config format compatible.** `~/.config/avell-kbd/config.json` written by older versions must keep loading.
- User-facing text is in English.

## Project layout

```
bin/avell-kbd            launcher (uses the system python3 on purpose)
keyboard_rgb/            the app (see the module list in keyboard_rgb/__init__.py)
packaging/               .deb build, driver Kbuild/dkms.conf, udev rule, unit, desktop entry
tests/                   unittest suite (mocked hardware) and tests/hardware/ (real laptop check)
install*.sh, uninstall.sh  manual install
```

## Releases (maintainers)

1. Bump `__version__` in `keyboard_rgb/__init__.py` and turn *Unreleased* into the new version in `CHANGELOG.md`.
2. Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. CI checks that the tag matches the version, then builds and publishes the release with the `.deb`.
