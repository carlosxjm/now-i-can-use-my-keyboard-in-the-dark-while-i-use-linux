"""Settings window (GTK4 + libadwaita).

The window never talks to the hardware directly: it edits the config file
(only the keys the user changed, see config.update) and the daemon applies it.
It reads the daemon's status file to show whether that is actually working.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 10) or \
        (Adw.get_major_version(), Adw.get_minor_version()) < (1, 4):
    raise SystemExit(f"Keyboard RGB needs GTK >= 4.10 and libadwaita >= 1.4 "
                     f"(found GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}, "
                     f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()})")

from . import APP_ID, APP_NAME, PROJECT_URL, SERVICE, __version__, config, self_command, self_env, status  # noqa: E402
from .effects import ANIMATED, EFFECT_IDS, EFFECTS, PRESETS, SELF_COLORED, compute_colors  # noqa: E402
from .hardware import default_backend  # noqa: E402
from .shortcut import current_label as shortcut_label  # noqa: E402

log = logging.getLogger(__name__)

HELP_URL = PROJECT_URL + "#troubleshooting"
WRITE_DELAY_MS = 120  # coalesce slider drags into a few config writes
POLL_MS = 500

CSS = """
.swatch { min-width: 34px; min-height: 34px; padding: 0; border-radius: 999px;
          border: 2px solid alpha(currentColor, 0.15); }
.swatch:focus-visible { outline: 2px solid @accent_color; outline-offset: 2px; }
.swatch.selected { border: 3px solid @accent_color; }
.preview-frame { border-radius: 12px; background: #15161a; }
""" + "".join(f".swatch-{i} {{ background: rgb({r},{g},{b}); }}\n"
              for i, (_, (r, g, b)) in enumerate(PRESETS))


def set_a11y_label(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def ensure_daemon() -> None:
    """Start the background service (systemd user unit, or a plain process)."""
    if status.read():
        return
    if shutil.which("systemctl"):
        r = subprocess.run(["systemctl", "--user", "start", SERVICE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return
    log.info("service unit not available; starting the daemon directly")
    try:
        subprocess.Popen(self_command("--daemon"), env=self_env(), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.error("cannot start the daemon: %s", e)


class KeyboardPreview(Gtk.Widget):
    """Animated drawing of a keyboard in the current colors."""

    ROWS = [15, 15, 14, 13, 12, 9]

    def __init__(self, win: "Window") -> None:
        super().__init__()
        self.win = win
        self.set_size_request(-1, 170)
        self.set_hexpand(True)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.t0 = time.monotonic()
        self.add_tick_callback(self._tick)

    def _tick(self, *_: Any) -> bool:
        cfg = self.win.cfg
        if cfg["on"] and self.win.effective_effect() in ANIMATED:
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snap: Gtk.Snapshot) -> None:
        w, h = self.get_width(), self.get_height()
        cfg, nz = self.win.cfg, self.win.nzones
        eff_cfg = dict(cfg, effect=self.win.effective_effect())
        colors = compute_colors(eff_cfg, time.monotonic() - self.t0, nz)
        bright = cfg["brightness"] / 100 if cfg["on"] else 0
        pad, gap = 16, 5
        kw = max(1.0, min((w - 2 * pad) / 15.0, (h - 2 * pad) / 6.0 * 1.15))
        kh = kw / 1.15
        ox, oy = (w - kw * 15) / 2, (h - kh * 6) / 2
        k = 0.12 + 0.88 * bright
        for row, n in enumerate(self.ROWS):
            key_w = 15 * kw / n
            for col in range(n):
                x = ox + col * key_w
                zi = min(nz - 1, int((x + key_w / 2 - ox) / (15 * kw) * nz))
                r, g, b = (1.0, 1.0, 1.0) if self.win.kind == "white" else (c / 255 for c in colors[zi])
                rect = Graphene.Rect().init(x + gap / 2, oy + row * kh + gap / 2, key_w - gap, kh - gap)
                color = Gdk.RGBA()
                color.red, color.green = 0.13 + r * k * 0.87, 0.13 + g * k * 0.87
                color.blue, color.alpha = 0.14 + b * k * 0.86, 1.0
                rr = Gsk.RoundedRect()  # keep a reference (avoids a use-after-free in PyGObject)
                rr.init_from_rect(rect, 4)
                snap.push_rounded_clip(rr)
                snap.append_color(color, rect)
                snap.pop()


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(540, 820)
        self.cfg = config.load()
        self.kb = default_backend()  # read-only here: only used to know what the hardware supports
        self.demo = not self.kb.available
        self.kind = self.kb.kind or "rgb"  # no driver: RGB demo preview
        self.nzones = self.kb.nzones if self.kb.available else 1
        self.zone_sel: int | None = None  # None = all zones
        self._updating = False
        self._pending: dict[str, Any] = {}
        self._flush_id = 0
        self._mtime = config.mtime()
        self._build()
        self.sync_widgets()
        self.update_banner()
        GLib.timeout_add(POLL_MS, self._poll)
        self.connect("close-request", lambda *_: (self._flush(), False)[1])

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append(f"About {APP_NAME}", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main menu", primary=True))
        view.add_top_bar(header)
        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self._on_banner_button)
        view.add_top_bar(self.banner)
        page = Adw.PreferencesPage()
        view.set_content(page)
        self.set_content(view)

        # Preview
        g = Adw.PreferencesGroup()
        frame = Gtk.Box(css_classes=["preview-frame"], overflow=Gtk.Overflow.HIDDEN)
        self.preview = KeyboardPreview(self)
        frame.append(self.preview)
        g.add(frame)
        page.add(g)

        # Light
        g = Adw.PreferencesGroup(title="Light")
        self.power_row = Adw.SwitchRow(title="Keyboard light")
        self.power_row.connect("notify::active", self._on_power)
        g.add(self.power_row)
        self.bright_row, self.bright = self._slider_row("Brightness", 1, 100, self._on_brightness)
        g.add(self.bright_row)
        page.add(g)

        # Effect
        g = Adw.PreferencesGroup(title="Effect")
        self.effect_row = Adw.ComboRow(title="Mode", model=Gtk.StringList.new([n for _, n in EFFECTS]))
        self.effect_row.connect("notify::selected", self._on_effect)
        g.add(self.effect_row)
        self.speed_row, self.speed = self._slider_row("Speed", 1, 100, self._on_speed)
        g.add(self.speed_row)
        page.add(g)

        # Color
        self.color_group = g = Adw.PreferencesGroup(title="Color")
        if self.nzones > 1:
            zrow = Adw.ActionRow(title="Apply to")
            box = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
            first = None
            for i, label in enumerate(["All"] + [f"Zone {n + 1}" for n in range(self.nzones)]):
                b = Gtk.ToggleButton(label=label, active=(i == 0))
                if first:
                    b.set_group(first)
                else:
                    first = b
                b.connect("toggled", self._on_zone, None if i == 0 else i - 1)
                box.append(b)
            zrow.add_suffix(box)
            g.add(zrow)

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=9,
                           min_children_per_line=5, column_spacing=4, row_spacing=8,
                           margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
                           homogeneous=True)
        self.swatches: list[tuple[Gtk.Button, list[int]]] = []
        for i, (name, rgb) in enumerate(PRESETS):
            b = Gtk.Button(css_classes=["swatch", f"swatch-{i}"], tooltip_text=name,
                           halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            set_a11y_label(b, f"{name} color")
            b.connect("clicked", lambda _b, c=rgb: self.set_color(list(c)))
            self.swatches.append((b, list(rgb)))
            flow.append(b)
        swatch_row = Adw.PreferencesRow(activatable=False, focusable=False)
        swatch_row.set_child(flow)
        g.add(swatch_row)

        crow = Adw.ActionRow(title="Custom color")
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                               valign=Gtk.Align.CENTER)
        set_a11y_label(self.color_btn, "Choose a custom color")
        self.color_btn.connect("notify::rgba", self._on_custom_color)
        crow.add_suffix(self.color_btn)
        crow.set_activatable_widget(self.color_btn)
        g.add(crow)
        page.add(g)

    def _slider_row(self, title: str, lo: int, hi: int, cb) -> tuple[Adw.ActionRow, Gtk.Scale]:
        row = Adw.ActionRow(title=title)
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        s.set_hexpand(True)
        s.set_size_request(220, -1)
        s.set_draw_value(True)
        s.set_value_pos(Gtk.PositionType.RIGHT)
        s.set_format_value_func(lambda _s, v: f"{int(v)}%")
        set_a11y_label(s, title)
        s.connect("value-changed", cb)
        row.add_suffix(s)
        return row, s

    # ------------------------------------------------------------------ state
    def effective_effect(self) -> str:
        eff = self.cfg["effect"]
        if self.demo:
            return eff
        return eff if eff in self.kb.supported_effects else "static"

    def _selected_color(self) -> list[int] | None:
        zones = self.cfg["zones"][:self.nzones]
        if self.zone_sel is not None:
            return zones[self.zone_sel]
        return zones[0] if all(z == zones[0] for z in zones) else None

    def sync_widgets(self) -> None:
        self._updating = True
        try:
            cfg = self.cfg
            color_ok = self.kind == "rgb"
            self.power_row.set_active(cfg["on"])
            label = shortcut_label()
            self.power_row.set_subtitle(f"Shortcut: {label}" if label else "")
            self.bright.set_value(cfg["brightness"])
            self.effect_row.set_selected(EFFECT_IDS.index(cfg["effect"]))
            self.effect_row.set_sensitive(color_ok)
            self.effect_row.set_subtitle("" if color_ok else "This keyboard only supports static light")
            self.speed.set_value(cfg["speed"])
            self.speed_row.set_sensitive(color_ok and cfg["effect"] in ANIMATED)
            self.color_group.set_visible(color_ok)
            self.color_group.set_sensitive(cfg["effect"] not in SELF_COLORED)
            self.color_group.set_description(
                "Not used by the selected effect" if cfg["effect"] in SELF_COLORED else None)
            cur = self._selected_color()
            for b, rgb in self.swatches:
                (b.add_css_class if rgb == cur else b.remove_css_class)("selected")
            # mixed zone colors under "All": show zone 1's color as the starting point
            rgba = Gdk.RGBA()
            rgba.parse("#%02x%02x%02x" % tuple(cur or cfg["zones"][0]))
            self.color_btn.set_rgba(rgba)
            set_a11y_label(self.preview, "Keyboard preview: " + (
                "light off" if not cfg["on"] else f"{dict(EFFECTS)[self.effective_effect()]}, "
                f"{cfg['brightness']}% brightness"))
        finally:
            self._updating = False
        self.preview.queue_draw()

    def update_banner(self) -> None:
        st = status.read()
        self._banner_action = None
        if self.demo:
            title, button, self._banner_action = (
                "No supported keyboard detected — showing a preview only", "Help", "help")
        elif not self.kb.writable():
            title, button, self._banner_action = (
                "No permission to control the keyboard (udev rule missing)", "Help", "help")
        elif not st:
            title, button, self._banner_action = (
                "The background service isn't running, so changes aren't applied", "Start", "start")
        elif st.get("error"):
            title, button, self._banner_action = (f"Keyboard error: {st['error']}", "Help", "help")
        else:
            self.banner.set_revealed(False)
            return
        self.banner.set_title(title)
        self.banner.set_button_label(button)
        self.banner.set_revealed(True)

    def _on_banner_button(self, *_: Any) -> None:
        if self._banner_action == "start":
            ensure_daemon()
            GLib.timeout_add(1000, lambda: (self.update_banner(), False)[1])
        elif self._banner_action == "help":
            Gtk.UriLauncher.new(HELP_URL).launch(self, None, None, None)

    # ------------------------------------------------------------------ writes
    def change(self, **kw: Any) -> None:
        """Update the local state now; write only these keys to the config shortly."""
        self.cfg = config.validate({**self.cfg, **kw})
        self._pending.update(kw)
        if not self._flush_id:
            self._flush_id = GLib.timeout_add(WRITE_DELAY_MS, self._flush)
        self.sync_widgets()

    def _flush(self) -> bool:
        self._flush_id = 0
        if self._pending:
            pending, self._pending = self._pending, {}
            try:
                self.cfg = config.update(**pending)
                self._mtime = config.mtime()
            except OSError as e:
                log.error("cannot save settings: %s", e)
                self.banner.set_title(f"Could not save settings: {e.strerror or e}")
                self.banner.set_button_label(None)
                self.banner.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def _poll(self) -> bool:
        m = config.mtime()
        if m != self._mtime and not self._pending:  # changed by the daemon, tray or --toggle
            self._mtime = m
            self.cfg = config.load()
            self.sync_widgets()
        if self.demo and self.kb.detect():
            self.demo = False  # driver appeared; the zone layout needs a fresh window
        self.update_banner()
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------ callbacks
    def _on_power(self, row: Adw.SwitchRow, _p: Any) -> None:
        if not self._updating:
            self.change(on=row.get_active())

    def _on_brightness(self, s: Gtk.Scale) -> None:
        if not self._updating:
            self.change(brightness=int(s.get_value()), on=True)

    def _on_effect(self, row: Adw.ComboRow, _p: Any) -> None:
        if not self._updating:
            self.change(effect=EFFECT_IDS[row.get_selected()])

    def _on_speed(self, s: Gtk.Scale) -> None:
        if not self._updating:
            self.change(speed=int(s.get_value()))

    def _on_zone(self, btn: Gtk.ToggleButton, idx: int | None) -> None:
        if btn.get_active():
            self.zone_sel = idx
            self.sync_widgets()

    def _on_custom_color(self, btn: Gtk.ColorDialogButton, _p: Any) -> None:
        if not self._updating:
            c = btn.get_rgba()
            self.set_color([round(c.red * 255), round(c.green * 255), round(c.blue * 255)])

    def set_color(self, rgb: list[int]) -> None:
        zones = [list(z) for z in self.cfg["zones"]]
        if self.zone_sel is None:
            zones = [list(rgb) for _ in zones]
        else:
            zones[self.zone_sel] = list(rgb)
        kw: dict[str, Any] = {"zones": zones}
        if self.cfg["effect"] in SELF_COLORED:
            kw["effect"] = "static"
        self.change(**kw)


class App(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb, accels in (("about", self._about, []),
                                 ("quit", lambda *_: self.quit(), ["<Control>q"])):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("window.close", ["<Control>w"])

    def do_activate(self) -> None:
        win = self.props.active_window or Window(self)
        win.present()

    def _about(self, *_: Any) -> None:
        Adw.AboutWindow(
            transient_for=self.props.active_window, application_name=APP_NAME,
            application_icon="input-keyboard", version=__version__,
            comments="Because apparently, Linux users need RGB too.",
            website=PROJECT_URL, issue_url=PROJECT_URL + "/issues",
            license_type=Gtk.License.GPL_2_0, developers=["carlosxjm"],
        ).present()


def run() -> int:
    config.ensure_exists()
    ensure_daemon()
    return App().run([sys.argv[0]])
