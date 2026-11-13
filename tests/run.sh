#!/usr/bin/env bash
# Runs the whole test suite with mocked hardware. Uses a private D-Bus session
# bus (dbus-run-session) and a virtual display (xvfb-run) when available, so it
# never touches your real desktop session. Extra args go to unittest (e.g. -v).
set -euo pipefail
cd "$(dirname "$0")/.."

cmd=(/usr/bin/python3 -m unittest discover -s tests -t tests "$@")
if command -v xvfb-run >/dev/null; then
  cmd=(xvfb-run -a "${cmd[@]}")
else
  echo "note: xvfb-run not found; GUI tests use your display if there is one" >&2
fi
if command -v dbus-run-session >/dev/null; then
  export KEYBOARD_RGB_TEST_PRIVATE_BUS=1
  cmd=(dbus-run-session -- "${cmd[@]}")
else
  echo "note: dbus-run-session not found; D-Bus integration tests are skipped" >&2
fi
exec "${cmd[@]}"
