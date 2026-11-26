"""Echo Companion on the desktop: Echo's face in a corner, speaking what your agents did.

GTK 4 with gtk4-layer-shell, so on Hyprland (and other wlroots compositors) Echo is an
overlay surface, not an ordinary window:
- it is never tiled, moved or resized by the compositor;
- it stays above other windows;
- it never takes keyboard focus (keyboard mode NONE);
- it reserves no screen space (exclusive zone 0).
Pointer input is limited to Echo and its speech bubble, so clicks elsewhere in the
corner reach the window below.

Echo slides in when there is something to say, animates in time with the real audio
(using the amplitude envelopes in the backend's speech events), shows an optional caption,
and slides away after a while. Clicking Echo opens a small menu: what's happening, each
agent, the latest report, recent discovery, while I was away, mute, pause, move, hide.

Run: ``python3 -m echo_companion`` (system Python with python-gobject and gtk4-layer-shell).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from ctypes import CDLL

# gtk4-layer-shell must be loaded before libwayland-client (it hooks wl_proxy).
try:
    CDLL("libgtk4-layer-shell.so")
except OSError:
    pass

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

import cairo  # noqa: E402

from echo_companion import face as facelib  # noqa: E402
from echo_companion.client import Backend  # noqa: E402

FACE = int(os.environ.get("ECHO_COMPANION_FACE", "112"))
MARGIN = 14
HIDE_AFTER = float(os.environ.get("ECHO_COMPANION_HIDE_AFTER", "45"))
BUBBLE_AFTER_SPEECH = 2.5  # the owner asked: text leaves 2–3 s after Echo stops talking
CORNERS = {"top-right": ("TOP", "RIGHT"), "top-left": ("TOP", "LEFT"),
           "bottom-right": ("BOTTOM", "RIGHT"), "bottom-left": ("BOTTOM", "LEFT")}

CSS = b"""
window.echo-companion { background: transparent; }
.echo-bubble {
  background: #111111; color: #e6e6e2; border: 1px solid #2c2c2c;
  border-radius: 14px 14px 4px 14px; padding: 10px 14px; font-size: 13.5px; font-weight: 550;
  box-shadow: 0 10px 30px rgba(0,0,0,.35);
}
.echo-bubble.left { border-radius: 14px 14px 14px 4px; }
.echo-bubble .who { color: #9a9a96; font-size: 11.5px; font-weight: 600; }
"""


class Companion(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="ai.echospeak.companion", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.backend = Backend()
        self.animator = facelib.Animator()
        self.appearance = facelib.Appearance()
        self.reduced = os.environ.get("ECHO_COMPANION_REDUCED_MOTION") == "1"
        self.corner = os.environ.get("ECHO_COMPANION_POSITION", "top-right")
        self.captions = True
        self.visible = False
        self.shown = 0.0          # 0 hidden .. 1 fully in
        self.target_shown = 0.0
        self.last_event = 0.0
        self.speaking_until = 0.0
        self.bubble_until = 0.0
        self.paused_until = 0.0
        self.muted = False
        self.after_speech_mode = "idle"
        self.speech_active = ""  # request id between speech.started and its speech.finished (chunks have gaps)
        self.connected = False
        self.enabled = True
        self.stop = threading.Event()
        self.pointer = None

    # ---- lifecycle --------------------------------------------------------------------
    def do_activate(self) -> None:  # noqa: N802 (GTK naming)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self, title="Echo Companion")
        self.window.add_css_class("echo-companion")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        if LayerShell is not None and LayerShell.is_supported():
            LayerShell.init_for_window(self.window)
            LayerShell.set_namespace(self.window, "echo-companion")
            LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
            LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)
            LayerShell.set_exclusive_zone(self.window, 0)
            self._apply_corner()
        else:
            print("gtk4-layer-shell is unavailable; Echo will be an ordinary window.", file=sys.stderr)

        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.bubble.add_css_class("echo-bubble")
        self.bubble.set_valign(Gtk.Align.START)
        self.bubble.set_margin_top(int(FACE * 0.18))
        self.who = Gtk.Label(xalign=0)
        self.who.add_css_class("who")
        self.caption = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, width_chars=22)
        self.bubble.append(self.who)
        self.bubble.append(self.caption)
        self.bubble.set_visible(False)
        width, height = facelib.canvas_size(FACE)
        self.area = Gtk.DrawingArea(content_width=width, content_height=height)
        self.area.set_draw_func(self._draw)
        self.box.append(self.bubble)
        self.box.append(self.area)
        self.window.set_child(self.box)

        click = Gtk.GestureClick(button=0)
        click.connect("released", self._clicked)
        self.area.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        motion.connect("leave", lambda *_: setattr(self, "pointer", None))
        self.area.add_controller(motion)
        self._build_menu()

        self.window.connect("realize", lambda *_: GLib.idle_add(self._update_input_region))
        self.area.add_tick_callback(self._tick)
        # Settings changes (on/off, position, captions, motion) reach the window within 30 s.
        GLib.timeout_add_seconds(30, lambda: (threading.Thread(target=self._load_settings, daemon=True).start(), True)[1])
        threading.Thread(target=self._load_settings, daemon=True).start()
        threading.Thread(target=self.backend.stream, args=("/companion/stream", self._on_event_thread,
                                                             self._on_connection_thread, self.stop), daemon=True).start()
        if os.environ.get("ECHO_COMPANION_SHOW") == "1":
            self.appear("idle")

    def do_shutdown(self) -> None:  # noqa: N802
        self.stop.set()
        Gtk.Application.do_shutdown(self)

    def _apply_corner(self) -> None:
        if LayerShell is None or not LayerShell.is_layer_window(self.window):
            return
        vertical, horizontal = CORNERS.get(self.corner, CORNERS["top-right"])
        for edge in ("TOP", "BOTTOM", "LEFT", "RIGHT"):
            LayerShell.set_anchor(self.window, getattr(LayerShell.Edge, edge), edge in (vertical, horizontal))
        self._set_slide()

    def _set_slide(self) -> None:
        """Slide horizontally from the anchored edge: margin from -(width) to MARGIN."""
        if LayerShell is None or not LayerShell.is_layer_window(self.window):
            self.window.set_opacity(self.shown)
            return
        vertical, horizontal = CORNERS.get(self.corner, CORNERS["top-right"])
        width = facelib.canvas_size(FACE)[0]
        eased = 1 - (1 - self.shown) ** 3
        LayerShell.set_margin(self.window, getattr(LayerShell.Edge, horizontal), int(-width * (1 - eased) + MARGIN * eased))
        LayerShell.set_margin(self.window, getattr(LayerShell.Edge, vertical), MARGIN)
        self.window.set_opacity(min(1.0, 0.15 + self.shown))

    # ---- appearance in time -----------------------------------------------------------
    def appear(self, mode: str) -> None:
        now = time.monotonic()
        self.last_event = now
        self.animator.set_mode(mode, now)
        self.target_shown = 1.0
        if not self.visible:
            self.visible = True
            self.window.present()
            GLib.idle_add(self._update_input_region)

    def _tick(self, widget, clock) -> bool:
        now = time.monotonic()
        if self.target_shown != self.shown:
            step = 1 / 18  # ~300 ms at 60 Hz
            self.shown = min(self.target_shown, self.shown + step) if self.target_shown > self.shown else max(self.target_shown, self.shown - step)
            self._set_slide()
            if self.shown == 0.0 and self.target_shown == 0.0 and self.visible:
                self.visible = False
                self.window.set_visible(False)
        mode = self.animator.state.mode
        if mode == "speaking" and not self.speech_active and now > self.speaking_until and not self.animator.envelope:
            self.animator.set_mode(self.after_speech_mode, now)
            self.after_speech_mode = "idle"
            self.bubble_until = now + BUBBLE_AFTER_SPEECH
        if mode in {"celebrating", "discovery", "confused"} and now - self.animator.state.mode_since > 2.2:
            self.animator.set_mode("idle", now)
        if self.bubble.get_visible() and now > self.bubble_until and mode != "speaking":
            self.bubble.set_visible(False)
            GLib.idle_add(self._update_input_region)
        if self.visible and mode in {"idle", "waiting"} and now - self.last_event > HIDE_AFTER and not self._menu_open():
            self.target_shown = 0.0
        if self.visible:
            self.animator.step(now, self.pointer, idle_wander=self.appearance.idle_motion)
            self.animator.state.reduced_motion = self.reduced
            self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, area, cr, width, height) -> None:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        facelib.draw(cr, width, height, FACE, self.animator.state, self.appearance)

    def _motion(self, _controller, x: float, y: float) -> None:
        width, height = facelib.canvas_size(FACE)
        self.pointer = (max(-1, min(1, (x - width / 2) / (FACE * 0.9))), max(-1, min(1, (y - height / 2) / (FACE * 0.9))))
        self.last_event = time.monotonic()

    def _update_input_region(self) -> bool:
        """Only Echo's face and the bubble take clicks; the rest of the surface is see-through."""
        surface = self.window.get_surface()
        if surface is None:
            return False
        region = cairo.Region()
        width, height = facelib.canvas_size(FACE)
        ok, bounds = self.area.compute_bounds(self.window)
        if ok:
            x, y = int(bounds.get_x()), int(bounds.get_y())
            region.union(cairo.RectangleInt(x + int((width - FACE) / 2), y + int(height / 2 - 0.58 * FACE), FACE, FACE))
        if self.bubble.get_visible():
            ok, bounds = self.bubble.compute_bounds(self.window)
            if ok:
                region.union(cairo.RectangleInt(int(bounds.get_x()), int(bounds.get_y()),
                                                int(bounds.get_width()), int(bounds.get_height())))
        surface.set_input_region(region)
        return False

    # ---- events from the backend (arrive on the stream thread) ----------------------------
    def _on_connection_thread(self, connected: bool) -> None:
        GLib.idle_add(self._on_connection, connected)

    def _on_connection(self, connected: bool) -> bool:
        if connected and not self.connected:
            threading.Thread(target=self._load_settings, daemon=True).start()
        self.connected = connected
        return False

    def _on_event_thread(self, name: str, data: dict) -> None:
        GLib.idle_add(self._on_event, name, data)

    def _on_event(self, name: str, data: dict) -> bool:
        now = time.monotonic()
        if now < self.paused_until or not self.enabled:
            return False
        if name == "commentary":
            self._show_caption(data.get("label", ""), data.get("text", ""),
                               seconds=60 if data.get("spoken") else min(10.0, 3 + len(data.get("text", "")) / 25))
            outcome = data.get("outcome")
            digest = data.get("digest") or {}
            if data.get("reason") == "needs_you":
                self.after_speech_mode = "waiting"
            elif outcome == "success":
                self.after_speech_mode = "celebrating"
            elif outcome == "failed":
                self.after_speech_mode = "confused"
            elif digest.get("discoveries"):
                self.after_speech_mode = "discovery"
            if not data.get("spoken"):
                self.appear(self.after_speech_mode if self.after_speech_mode != "idle" else "idle")
                self.after_speech_mode = "idle"
            return False
        request = data.get("request") or {}
        if request.get("channel") not in {"companion", "preview", "echo"}:
            return False
        if name == "speech.started":
            self.speech_active = str(request.get("id") or "")
            self.appear("thinking")  # synthesising the first chunk
            if request.get("channel") != "echo":
                self._show_caption(request.get("source", ""), request.get("text", ""), seconds=60)
        elif name == "speech.chunk_started":
            # Event time is the backend's wall clock; convert it to our monotonic clock.
            lag = max(0.0, time.time() - float(data.get("at") or time.time()))
            start = now - lag
            self.animator.speak(data.get("envelope") or [], int(data.get("envelope_hz") or 50), start)
            self.speaking_until = start + float(data.get("duration") or 0) + 0.1
            self.appear("speaking")
        elif name == "speech.finished":
            if request.get("id") != self.speech_active:
                return False  # a queued line was cancelled or skipped; the current one is still playing
            self.speech_active = ""
            self.speaking_until = min(self.speaking_until, now + 0.1)
            if request.get("status") == "failed" and self.animator.state.mode != "speaking":
                self.after_speech_mode = "confused"
        return False

    def _show_caption(self, who: str, text: str, seconds: float) -> None:
        if not self.captions or not text:
            return
        known = {"claude-code": "Claude Code", "codex": "Codex", "echo": "Echo", "companion": "Echo", "": "Echo"}
        label = known.get(who) or (who if any(c.isupper() for c in who) else who.replace("-", " ").title())
        self.who.set_text(label)
        self.caption.set_text(text if len(text) <= 260 else text[:257] + "…")
        self.bubble.set_visible(True)
        self.bubble_until = time.monotonic() + seconds
        GLib.idle_add(self._update_input_region)

    # ---- settings ------------------------------------------------------------------------
    def _load_settings(self) -> None:
        try:
            avatar = self.backend.get("/avatar/config", timeout=5)
        except Exception:
            avatar = None
        try:
            settings = (self.backend.get("/settings", timeout=5) or {}).get("settings", {})
        except Exception:
            settings = {}
        GLib.idle_add(self._apply_settings, avatar, settings)

    def _apply_settings(self, avatar, settings) -> bool:
        if avatar:
            self.appearance = facelib.Appearance.from_config(avatar)
        if settings:
            self.enabled = bool(settings.get("companion_enabled", True)) or os.environ.get("ECHO_COMPANION_SHOW") == "1"
            if not self.enabled and self.visible:
                self.target_shown = 0.0
            corner = str(settings.get("companion_position") or self.corner)
            if corner in CORNERS and corner != self.corner:
                self.corner = corner
                self._apply_corner()
            self.captions = bool(settings.get("companion_captions", True))
            self.reduced = self.reduced or settings.get("companion_animation") == "reduced"
        return False

    # ---- menu ---------------------------------------------------------------------------
    def _build_menu(self) -> None:
        menu = Gio.Menu()
        ask = Gio.Menu()
        for label, topic in (("What's happening?", "activity"), ("What is Claude Code doing?", "agent:claude-code"),
                             ("What is Codex doing?", "agent:codex"), ("What is Echo doing?", "agent:echo"),
                             ("Latest report", "latest_report"), ("Recent discovery", "discovery"),
                             ("While I was away", "recap")):
            ask.append(label, f"app.ask::{topic}")
        menu.append_section(None, ask)
        control = Gio.Menu()
        control.append("Mute commentary", "app.mute")
        control.append("Pause for an hour", "app.pause")
        place = Gio.Menu()
        for corner in CORNERS:
            place.append(corner.replace("-", " ").capitalize(), f"app.move::{corner}")
        control.append_submenu("Move to", place)
        control.append("Hide", "app.hide")
        menu.append_section(None, control)
        for name, handler, typed in (("ask", self._ask, True), ("move", self._move, True), ("mute", self._mute, False),
                                     ("pause", self._pause, False), ("hide", self._hide, False)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s") if typed else None)
            action.connect("activate", handler)
            self.add_action(action)
        self.popover = Gtk.PopoverMenu.new_from_model(menu)
        self.popover.set_parent(self.area)
        self.popover.set_has_arrow(False)
        self.popover.connect("closed", lambda *_: GLib.idle_add(self._update_input_region))

    def _menu_open(self) -> bool:
        return bool(getattr(self, "popover", None) and self.popover.get_visible())

    def _clicked(self, gesture, _n, x, y) -> None:
        self.last_event = time.monotonic()
        self.animator.set_mode("listening", time.monotonic())
        self.popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        # While the menu is open the whole surface takes input, so the menu is clickable.
        surface = self.window.get_surface()
        if surface is not None:
            surface.set_input_region(None)
        self.popover.popup()

    def _ask(self, _action, value) -> None:
        topic = value.get_string()
        self.animator.set_mode("thinking", time.monotonic())

        def work():
            try:
                answer = self.backend.post("/companion/ask", {"topic": topic, "speak": not self.muted}, timeout=20)
                GLib.idle_add(self._show_caption, "companion", answer.get("text", ""), 25)
            except Exception:
                GLib.idle_add(self._show_caption, "companion", "I can't reach EchoSpeak right now.", 8)
                GLib.idle_add(lambda: self.animator.set_mode("confused", time.monotonic()) or False)
        threading.Thread(target=work, daemon=True).start()

    def _mute(self, *_args) -> None:
        self.muted = not self.muted
        threading.Thread(target=lambda: self._safe_post("/media-runtime/voice/speak/mute",
                                                        {"channel": "companion", "muted": self.muted}), daemon=True).start()
        self._show_caption("companion", "Muted. I'll keep captions." if self.muted else "Unmuted.", 4)

    def _pause(self, *_args) -> None:
        self.paused_until = time.monotonic() + 3600
        self._show_caption("companion", "Taking an hour off. Your agents keep working.", 4)
        GLib.timeout_add(4500, lambda: self._hide() or False)

    def _hide(self, *_args) -> None:
        self.target_shown = 0.0

    def _move(self, _action, value) -> None:
        self.corner = value.get_string()
        self._apply_corner()
        threading.Thread(target=lambda: self._safe_post("/settings", {"companion_position": self.corner}, "PUT"), daemon=True).start()

    def _safe_post(self, path: str, body: dict, method: str = "POST") -> None:
        try:
            self.backend._request(method, path, body, timeout=5)
        except Exception:
            pass


def main() -> int:
    return Companion().run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
