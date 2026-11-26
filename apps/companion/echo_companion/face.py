"""Echo's face for the desktop companion, drawn with Cairo.

This is a port of ``apps/web/src/components/EchoFace.tsx``: the same rounded square, the
same dark pill eyes, the same proportions, motions and colours (from the owner's saved
avatar config). The companion looks like the Echo the owner already knows, not a new
mascot.

Proportions, in units of the face size F:
- body: rounded square, corner radius ``min(50%, body_roundness × 1.93%)``
- eyes: width ``0.115F``, height ``0.2F``, gap ``0.15F``, scaled by ``eye_size``
- gaze: eyes move up to ``0.15F`` across and ``0.11F`` down; the face turns slightly
- blink: eyes squash to 8% height. Happy: short eyes with flat bottoms, raised ``0.03F``

The web face has idle, listening, thinking, working, speaking and error. The companion adds
celebrating, confused, discovery, waiting and sleeping.

Speaking is driven by the real audio: ``amplitude`` (0–1, from the speech envelope of the
clip that is playing) squashes the eyes and pulses the aura, the same way the web face's
talk animation does, but in time with the sound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MODES = ("idle", "listening", "thinking", "speaking", "working", "error",
         "celebrating", "confused", "discovery", "waiting", "sleeping")


def hex_rgb(value: str, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


@dataclass
class Appearance:
    body: tuple[float, float, float] = (1.0, 1.0, 1.0)
    eye: tuple[float, float, float] = (0.0, 0.0, 0.0)
    glow: tuple[float, float, float] = (1.0, 1.0, 1.0)
    roundness: float = 14.0
    eye_size: float = 1.0
    enable_glow: bool = True
    breathing_speed: float = 1.0
    idle_motion: bool = True

    @classmethod
    def from_config(cls, config: dict | None) -> "Appearance":
        c = dict(config or {})
        clamp = lambda v, lo, hi, d: min(hi, max(lo, float(v))) if isinstance(v, (int, float)) else d  # noqa: E731
        return cls(
            body=hex_rgb(c.get("body_color", "#ffffff"), (1, 1, 1)),
            eye=hex_rgb(c.get("eye_color", "#000000"), (0, 0, 0)),
            glow=hex_rgb(c.get("glow_color", "#ffffff"), (1, 1, 1)),
            roundness=clamp(c.get("body_roundness"), 4, 40, 14.0),
            eye_size=clamp(c.get("eye_size"), 0.5, 2.0, 1.0),
            enable_glow=bool(c.get("enable_glow", True)),
            breathing_speed=clamp(c.get("breathing_speed"), 0.4, 2.6, 1.0),
            idle_motion=bool(c.get("enable_idle_activities", True)) and c.get("idle_activity", "auto") != "none",
        )


@dataclass
class FaceState:
    mode: str = "idle"
    t: float = 0.0                      # seconds, monotonic
    look_x: float = 0.0                 # -1..1 (smoothed)
    look_y: float = 0.0
    blink: float = 0.0                  # 0 open .. 1 closed
    amplitude: float = 0.0              # 0..1 while speaking
    mode_since: float = 0.0
    reduced_motion: bool = False
    extras: dict = field(default_factory=dict)
    body_pose: tuple[float, float, float] | None = None
    transition_pose: tuple[float, float, float] | None = None


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


def canvas_size(face: float) -> tuple[int, int]:
    """Width and height needed around a face of size `face` (aura, float, shadow, z's)."""
    return int(face * 1.7), int(face * 1.75)


def _body_pose(F: float, t: float, mode: str, since: float, still: bool,
               amp: float, look: Appearance) -> tuple[float, float, float]:
    period = 6.0 / look.breathing_speed
    dy, rot, scale = 0.0, 0.0, 1.0
    if not still:
        if look.idle_motion or mode != "idle":
            dy = -0.045 * F * _ease(0.5 - 0.5 * math.cos(2 * math.pi * t / period))
        if mode == "speaking":
            dy = -0.012 * F - 0.03 * F * amp
            rot = math.radians(1.2) * math.sin(t * 2 * math.pi / 0.7)
        elif mode == "working":
            dy = -0.018 * F * abs(math.sin(t * math.pi / 0.9))
        elif mode == "thinking":
            rot = math.radians(3) * math.sin(t * 2 * math.pi / 2.6) ** 2
        elif mode == "error":
            rot = math.radians(-3)
        elif mode == "confused":
            rot = math.radians(-7) + math.radians(1.5) * math.sin(t * 2.2)
        elif mode == "sleeping":
            dy = -0.02 * F * _ease(0.5 - 0.5 * math.cos(2 * math.pi * t / 7.5))
            rot = math.radians(4)
        elif mode == "listening":
            scale = 1.03
        if mode == "celebrating" and since < 1.4:  # echoHop
            p = since / 0.9
            if p < 1:
                hop = [0, -0.08, 0, -0.02, 0]
                seg = min(3, int(p * 4))
                local = p * 4 - seg
                dy += F * (hop[seg] + (hop[seg + 1] - hop[seg]) * _ease(local))
                rot += math.radians([0, -3, 2, 0, 0][seg] + ([0, -3, 2, 0, 0][seg + 1] - [0, -3, 2, 0, 0][seg]) * local)
        if mode == "discovery" and since < 0.5:  # echoPerk
            dy += -0.06 * F * math.sin(math.pi * since / 0.5)
        if mode == "error" and since < 0.5:  # echoShake
            rot += math.radians(5) * math.sin(since * 4 * math.pi / 0.5) * (1 - since / 0.5)
    return dy, rot, scale


def draw(cr, width: float, height: float, face: float, state: FaceState, look: Appearance) -> None:
    """Draw one frame centred in (width, height). Cairo context `cr` is not cleared."""

    F = face
    t = state.t
    mode = state.mode if state.mode in MODES else "idle"
    since = max(0.0, t - state.mode_since)
    still = state.reduced_motion
    amp = max(0.0, min(1.0, state.amplitude)) if mode == "speaking" else 0.0

    pose = _body_pose(F, t, mode, since, still, amp, look)
    if state.transition_pose is not None and not still:
        progress = _ease(since / 0.18)
        pose = tuple(old + (new - old) * progress for old, new in zip(state.transition_pose, pose))
    state.body_pose = pose
    dy, rot, scale = pose

    cx, cy = width / 2, height / 2 - 0.08 * F
    # ---- shadow --------------------------------------------------------------------
    lift = -dy / (0.045 * F) if dy < 0 else 0.0
    sx = 1 - 0.16 * min(1.0, lift)
    cr.save()
    cr.translate(cx, cy + F * 0.5 + F * 0.13)
    cr.scale(0.31 * F * sx, 0.03 * F)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.set_source_rgba(0, 0, 0, 0.45 - 0.15 * min(1.0, lift))
    cr.fill()

    cr.save()
    cr.translate(cx, cy + dy)
    cr.rotate(rot)
    cr.scale(scale, scale)

    radius = F * min(0.5, look.roundness * 1.93 / 100.0)
    # ---- aura: a glow in Echo's own shape --------------------------------------------
    # The owner asked for no white highlight behind Echo on the desktop: only the coloured
    # state cues remain (red for an error, gold for a discovery).
    if look.enable_glow and mode in {"error", "discovery"}:
        strength = 0.38
        grow = 1.02 + 0.04 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 4.8))
        colour = look.glow
        if mode == "speaking":
            grow, strength = 1.04 + 0.14 * amp, 0.38 * (1.0 + 0.6 * amp)
        elif mode in {"thinking", "working"}:
            grow = 1.02 + 0.08 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / (1.8 if mode == "thinking" else 1.1)))
        elif mode == "error":
            colour = (0.898, 0.282, 0.302)
        elif mode == "celebrating":
            grow, strength = 1.1 + 0.06 * math.sin(t * 9), 0.55
        elif mode == "discovery":
            colour, grow, strength = (1.0, 0.86, 0.45), 1.12, 0.5
        elif mode == "waiting":
            strength = 0.25 + 0.12 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 2.4))
        for i in range(10, 0, -1):  # a soft blur from stacked translucent shapes
            spread = F * grow * (1 + 0.05 * i)
            cr.set_source_rgba(*colour, strength * 0.045)
            _rounded_rect(cr, -spread / 2, -spread / 2, spread, spread, radius * (1 + 0.05 * i))
            cr.fill()

    # ---- body ----------------------------------------------------------------------
    turn_x = 0.0 if still else state.look_x
    squash = 1 - 0.04 * abs(turn_x)  # rotateY(look * 11deg) seen face-on
    cr.save()
    cr.scale(squash, 1.0)
    cr.set_source_rgba(0, 0, 0, 0.30)  # drop shadow
    _rounded_rect(cr, -F / 2, -F / 2 + F * 0.05, F, F, radius)
    cr.fill()
    cr.set_source_rgb(*look.body)
    _rounded_rect(cr, -F / 2, -F / 2, F, F, radius)
    cr.fill_preserve()
    cr.save()
    cr.clip()
    cr.set_source_rgba(0, 0, 0, 0.06)  # inset bottom edge
    cr.rectangle(-F / 2, F / 2 - 0.02 * F, F, 0.02 * F)
    cr.fill()
    if mode in {"thinking", "working"} and not still:  # the light sweep
        cycle = 2.6 if mode == "thinking" else 1.5
        p = (t % cycle) / cycle
        if p > 0.45:
            q = (p - 0.45) / 0.55
            x = -F * 1.1 + q * F * 1.8
            alpha = 0.8 * math.sin(math.pi * q)
            for k in range(6):
                cr.set_source_rgba(1, 1, 1, alpha * 0.12)
                cr.save()
                cr.translate(x + k * F * 0.03, 0)
                cr.transform(_skew(-0.25))
                cr.rectangle(0, -F, F * 0.18 - k * F * 0.025, 2 * F)
                cr.restore()
                cr.fill()
    cr.restore()
    cr.restore()

    # ---- eyes ----------------------------------------------------------------------
    es = look.eye_size
    ew, eh, gap = 0.115 * F * es, 0.2 * F * es, 0.15 * F
    gx, gy = state.look_x, state.look_y
    if not still:
        if mode == "thinking":
            p = (t % 2.6) / 2.6
            gx, gy = _path(p, [(0.0, 0.2, -0.45), (0.35, -0.23, -0.55), (0.7, 0.1, -0.18), (1.0, 0.2, -0.45)])
        elif mode == "working":
            gx, gy = 0.33 * math.sin(t * 2 * math.pi / 1.5), 0.09
        elif mode == "waiting":
            gx, gy = -0.55, 0.45  # looking down toward the screen
        elif mode == "confused":
            gx, gy = 0.25 * math.sin(t * 1.3), -0.2
        elif mode == "discovery":
            gx, gy = 0.0, -0.35
        elif mode == "sleeping":
            gx, gy = 0.0, 0.2
    ox, oy = gx * 0.15 * F, gy * 0.11 * F
    sy, sxe = 1.0, 1.0
    shape = "pill"
    if mode == "working":
        sy = 0.62
    elif mode == "listening":
        sxe, sy = 1.06, 1.1
    elif mode == "speaking":
        sxe, sy = 1.0 + 0.06 * amp, 1.0 - 0.28 * amp
    elif mode == "error":
        sy = 0.8
    elif mode == "discovery":
        sxe, sy = 1.12, 1.18
    elif mode in {"celebrating"} or (mode == "idle" and state.extras.get("happy")):
        shape = "happy"
    elif mode == "sleeping":
        shape = "closed"
    blink = 0.0 if still else state.blink
    if mode in {"sleeping"}:
        blink = 0.0
    cr.save()  # still inside the body's frame: the eyes tilt, bob and scale with Echo
    cr.translate(ox, oy)
    if mode == "error":
        cr.rotate(math.radians(-8))
        cr.translate(0, 0.03 * F)
    cr.set_source_rgb(*look.eye)
    for side in (-1, 1):
        w = ew * sxe
        h = eh * sy
        if mode == "confused" and side == 1:
            h *= 0.72
        x = side * (gap / 2 + ew / 2) - w / 2
        if shape == "happy":
            hh = 0.09 * F * es
            _happy_eye(cr, x, -0.03 * F - hh / 2, w, hh)
        elif shape == "closed":
            _rounded_rect(cr, x - w * 0.1, -0.012 * F, w * 1.2, 0.024 * F, 0.012 * F)
        else:
            h = max(h * (1 - 0.92 * blink), 0.02 * F)
            y0 = -eh * sy * 0.6 + (eh * sy - h) * 0.6  # transform-origin 50% 60%
            _rounded_rect(cr, x, y0, w, h, min(w, h) / 2)
        cr.fill()
    cr.restore()
    cr.restore()  # the body's frame

    # ---- extras: z's while sleeping, a question mark when confused, sparkles ---------------
    if mode == "sleeping" and not still:
        for i in range(3):
            p = ((t / 2.4) + i / 3) % 1.0
            size = F * (0.09 + 0.05 * p)
            _glyph(cr, "z", cx + F * (0.42 + 0.25 * p), cy - F * (0.35 + 0.45 * p), size, 0.85 * (1 - p))
    if mode == "confused":
        _glyph(cr, "?", cx + F * 0.58, cy - F * 0.55 + 0.02 * F * math.sin(t * 3), F * 0.26, 0.9)
    if mode in {"celebrating", "discovery"} and not still and since < 2.0:
        for i in range(6):
            a = i / 6 * 2 * math.pi + t * 0.8
            d = F * (0.62 + 0.18 * min(1.0, since / 0.6))
            _sparkle(cr, cx + math.cos(a) * d, cy + dy + math.sin(a) * d * 0.9, F * 0.045, 1 - since / 2.0,
                     (1.0, 0.86, 0.45) if mode == "discovery" else look.glow)


def _skew(k: float):
    import cairo
    return cairo.Matrix(1, 0, k, 1, 0, 0)


def _path(p: float, keys: list[tuple[float, float, float]]) -> tuple[float, float]:
    for (p0, x0, y0), (p1, x1, y1) in zip(keys, keys[1:]):
        if p0 <= p <= p1:
            k = _ease((p - p0) / (p1 - p0))
            return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    return keys[0][1], keys[0][2]


def _happy_eye(cr, x: float, y: float, w: float, h: float) -> None:
    """border-radius: 999px 999px 40% 40% — a dome with softened lower corners."""
    r = w / 2
    cr.new_sub_path()
    cr.arc(x + r, y + r, r, math.pi, 2 * math.pi)
    lower = min(0.4 * w, h - r) if h > r else 0.0
    cr.line_to(x + w, y + h - lower)
    if lower > 0:
        cr.curve_to(x + w, y + h, x + w, y + h, x + w - lower, y + h)
        cr.line_to(x + lower, y + h)
        cr.curve_to(x, y + h, x, y + h, x, y + h - lower)
    else:
        cr.line_to(x + w, y + h)
        cr.line_to(x, y + h)
    cr.close_path()


def _glyph(cr, text: str, x: float, y: float, size: float, alpha: float) -> None:
    cr.save()
    cr.select_font_face("Sans", 0, 1)
    cr.set_font_size(size)
    cr.set_source_rgba(1, 1, 1, max(0.0, alpha))
    cr.move_to(x, y)
    cr.show_text(text)
    cr.restore()


def _sparkle(cr, x: float, y: float, r: float, alpha: float, colour) -> None:
    cr.save()
    cr.translate(x, y)
    cr.set_source_rgba(*colour, max(0.0, min(1.0, alpha)))
    cr.move_to(0, -r)
    for i in range(1, 8):
        a = i * math.pi / 4 - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.3
        cr.line_to(math.cos(a) * rr, math.sin(a) * rr)
    cr.close_path()
    cr.fill()
    cr.restore()


class Animator:
    """Gaze, blinks and amplitude over time: the per-frame state the face is drawn from."""

    def __init__(self, seed: int = 7):
        import random

        self.random = random.Random(seed)
        self.state = FaceState()
        self.target = (0.0, 0.0)
        self.next_blink = 1.8
        self.blink_until = 0.0
        self.double_blink_at = -1.0
        self.envelope: list[float] = []
        self.envelope_hz = 50
        self.envelope_start = 0.0

    def set_mode(self, mode: str, now: float) -> None:
        if mode != self.state.mode:
            self.state.transition_pose = self.state.body_pose
            self.state.mode = mode
            self.state.mode_since = now

    def speak(self, envelope: list[float], hz: int, start: float) -> None:
        self.envelope, self.envelope_hz, self.envelope_start = list(envelope), int(hz or 50), start

    def step(self, now: float, pointer: tuple[float, float] | None = None, idle_wander: bool = True) -> FaceState:
        s = self.state
        s.t = now
        if pointer is not None:
            self.target = pointer
        elif idle_wander:
            self.target = (math.sin(now * 0.55) * 0.55, math.sin(now * 0.9) * 0.25)
        s.look_x += (self.target[0] - s.look_x) * 0.12
        s.look_y += (self.target[1] - s.look_y) * 0.12
        # blinks: 2.4–6 s apart, sometimes a double blink
        if now >= self.next_blink:
            self.blink_until = now + 0.13
            self.double_blink_at = now + 0.29 if self.random.random() < 0.22 else -1.0
            self.next_blink = now + 2.4 + self.random.random() * 3.6
        if 0 < self.double_blink_at <= now:
            self.blink_until = now + 0.12
            self.double_blink_at = -1.0
        s.blink = 1.0 if now < self.blink_until else 0.0
        if self.envelope:
            index = int((now - self.envelope_start) * self.envelope_hz)
            s.amplitude = self.envelope[index] if 0 <= index < len(self.envelope) else 0.0
            if index >= len(self.envelope):
                self.envelope = []
        else:
            s.amplitude *= 0.8
        return s
