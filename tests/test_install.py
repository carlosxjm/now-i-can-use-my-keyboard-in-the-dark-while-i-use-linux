"""install.sh / uninstall.sh against a temporary HOME, with systemctl stubbed out."""

import os
import stat
import subprocess
import unittest

from helpers import REPO, IsolatedTestCase

STUB = """#!/bin/sh
echo "$0 $*" >> "$STUB_LOG"
exit 0
"""


def have_gtk_bindings() -> bool:
    code = ("import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1');"
            "from gi.repository import Adw, Gtk")
    return subprocess.run(["/usr/bin/python3", "-c", code], capture_output=True).returncode == 0


@unittest.skipUnless(have_gtk_bindings(), "system GTK4/libadwaita bindings not installed")
class InstallScriptTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.home = os.environ["HOME"]
        stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(stubs)
        for name in ("systemctl", "update-desktop-database"):
            path = os.path.join(stubs, name)
            with open(path, "w") as fh:
                fh.write(STUB)
            os.chmod(path, 0o755)
        self.log = os.path.join(self.tmp, "stub.log")
        self.env = dict(os.environ, PATH=stubs + os.pathsep + os.environ["PATH"], STUB_LOG=self.log)
        self.env.pop("XDG_CONFIG_HOME")  # scripts must default to ~/.config

    def run_script(self, name, *args):
        return subprocess.run([os.path.join(REPO, name), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def p(self, *parts):
        return os.path.join(self.home, *parts)

    def snapshot(self):
        out = {}
        for root, dirs, files in os.walk(self.home):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".cache")]  # generated at runtime
            for f in files:
                path = os.path.join(root, f)
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, self.home)] = fh.read()
        return out

    def test_install_creates_expected_files(self):
        r = self.run_script("install.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        launcher = self.p(".local/bin/avell-kbd")
        self.assertTrue(os.stat(launcher).st_mode & stat.S_IXUSR)
        self.assertTrue(os.path.isfile(self.p(".local/lib/my-keyboard-app/keyboard_rgb/__init__.py")))
        with open(self.p(".config/systemd/user/avell-kbd.service")) as fh:
            self.assertIn(f"ExecStart={launcher} --daemon", fh.read())
        with open(self.p(".local/share/applications/io.github.carlosxjm.KeyboardRGB.desktop")) as fh:
            self.assertIn(f"Exec={launcher}\n", fh.read())
        with open(self.log) as fh:
            calls = fh.read()
        self.assertIn("--user reenable avell-kbd.service", calls)
        self.assertIn("--user restart avell-kbd.service", calls)
        # the installed launcher works and finds its package
        out = subprocess.run([launcher, "--version"], capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_reinstall_is_idempotent(self):
        self.run_script("install.sh")
        first = self.snapshot()
        r = self.run_script("install.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), first)
        leftovers = [f for f in os.listdir(self.p(".local/lib")) if f != "my-keyboard-app"]
        self.assertEqual(leftovers, [])

    def test_install_replaces_legacy_files(self):
        legacy_desktop = self.p(".local/share/applications/vet.local.AvellKbd.desktop")
        os.makedirs(os.path.dirname(legacy_desktop))
        open(legacy_desktop, "w").close()
        os.makedirs(self.p(".local/bin"))
        with open(self.p(".local/bin/avell-kbd"), "w") as fh:
            fh.write("#!/usr/bin/env python3\n# old single-file version\n")
        self.run_script("install.sh")
        self.assertFalse(os.path.exists(legacy_desktop))
        with open(self.p(".local/bin/avell-kbd")) as fh:
            self.assertIn("KEYBOARD_RGB_LAUNCHER", fh.read())

    def test_uninstall_removes_only_project_files_and_keeps_settings(self):
        self.run_script("install.sh")
        # user data and unrelated files that must survive
        os.makedirs(self.p(".config/avell-kbd"), exist_ok=True)
        with open(self.p(".config/avell-kbd/config.json"), "w") as fh:
            fh.write('{"on": false}')
        open(self.p(".local/bin/other-tool"), "w").close()
        open(self.p(".local/share/applications/other.desktop"), "w").close()
        open(self.p(".config/systemd/user/other.service"), "w").close()

        r = self.run_script("uninstall.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for gone in (".local/bin/avell-kbd", ".local/lib/my-keyboard-app",
                     ".local/share/applications/io.github.carlosxjm.KeyboardRGB.desktop",
                     ".config/systemd/user/avell-kbd.service"):
            self.assertFalse(os.path.exists(self.p(gone)), gone)
        for kept in (".config/avell-kbd/config.json", ".local/bin/other-tool",
                     ".local/share/applications/other.desktop", ".config/systemd/user/other.service"):
            self.assertTrue(os.path.exists(self.p(kept)), kept)

    def test_uninstall_without_install_is_harmless(self):
        r = self.run_script("uninstall.sh")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_driver_uninstall_requires_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = self.run_script("uninstall.sh", "--driver")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo", r.stderr)

    def test_scripts_refuse_wrong_privileges(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = self.run_script("install-driver.sh")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sudo", r.stderr)
