"""Echo's face for the desktop companion, drawn with Cairo.

The rounded body, pill eyes and saved avatar colours match the web face. Subtle
changes in gaze and eye shape convey the Companion's states. Speaking follows
the amplitude envelope of the audio being played.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple

MODES = ("idle", "listening", "thinking", "speaking", "working", "error",
         "celebrating", "confused", "discovery", "waiting", "sleeping")
POSE_BLEND_SECONDS = 0.28
BLINK_CLOSE_SECONDS = 0.06
BLINK_REOPEN_AT = 0.085
BLINK_OPEN_SECONDS = 0.11
THINKING_CYCLE_SECONDS = 2.6
THINKING_GAZE = (
    (0.0, 0.20, -0.45),
    (0.35, -0.23, -0.55),
    (0.70, 0.10, -0.18),
    (1.0, 0.20, -0.45),
)


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


class Expression(NamedTuple):
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    eye_height: float = 1.0
    eye_width: float = 1.0
    asymmetry: float = 0.0
    eye_offset: float = 0.0
    cue: float = 0.0


EXPRESSIONS = {
    "listening": Expression(eye_height=1.08, eye_offset=-0.008),
    "thinking": Expression(gaze_x=-0.16, gaze_y=-0.36, eye_height=0.94),
    "working": Expression(gaze_y=0.12, eye_height=0.9),
    "error": Expression(gaze_y=0.1, eye_height=0.82, cue=-1.0),
    "celebrating": Expression(eye_height=0.86, eye_offset=-0.017),
    "confused": Expression(gaze_x=0.12, gaze_y=-0.16, eye_height=0.95, asymmetry=-0.10),
    "discovery": Expression(gaze_y=-0.27, eye_height=1.12, eye_offset=-0.008, cue=0.75),
    "waiting": Expression(gaze_x=-0.18, gaze_y=0.24, eye_height=0.94),
    "sleeping": Expression(eye_height=0.13, eye_offset=0.01),
}
NEUTRAL = Expression()


@dataclass
class FaceState:
    mode: str = "idle"
    t: float = 0.0                      # seconds, monotonic
    look_x: float = 0.0                 # -1..1 (smoothed)
    look_y: float = 0.0
    blink: float = 0.0                  # 0 open .. 1 closed
    amplitude: float = 0.0              # 0..1 while speaking
    speech_motion: float | None = None  # smoothed envelope for rendering
    mode_since: float = 0.0
    reduced_motion: bool = False
    body_pose: tuple[float, float, float] | None = None
    transition_pose: tuple[float, float, float] | None = None
    expression: Expression | None = None
    transition_expression: Expression | None = None


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


def _thinking_gaze(phase: float) -> tuple[float, float]:
    for (start, x, y), (end, next_x, next_y) in zip(THINKING_GAZE, THINKING_GAZE[1:]):
        if phase <= end:
            progress = _ease((phase - start) / (end - start))
            return x + (next_x - x) * progress, y + (next_y - y) * progress
    return THINKING_GAZE[0][1], THINKING_GAZE[0][2]


def canvas_size(face: float) -> tuple[int, int]:
    """Keep the existing canvas so the face, caption and input regions stay anchored."""
    return int(face * 1.7), int(face * 1.75)


def draw(cr, width: float, height: float, face: float, state: FaceState, look: Appearance) -> None:
    """Draw one frame centred in (width, height). Cairo context `cr` is not cleared."""
    F = face
    mode = state.mode
    still = state.reduced_motion
    speech_motion = state.amplitude if state.speech_motion is None else state.speech_motion
    amp = max(0.0, min(1.0, speech_motion)) if mode == "speaking" else 0.0

    expression = EXPRESSIONS.get(mode, NEUTRAL)
    age = max(0.0, state.t - state.mode_since)
    if amp:
        expression = Expression(eye_height=1.0 - 0.28 * amp, eye_width=1.0 + 0.06 * amp)

    dy, rotation = 0.0, 0.0
    if not still:
        breath = (1.0 - math.cos(math.tau * state.t * look.breathing_speed / 6.0)) / 2.0
        if mode == "speaking":
            dy = -F * (0.012 + 0.03 * amp)
            rotation = math.radians(1.2) * math.sin(math.tau * age / 0.7)
        elif mode == "thinking":
            phase = (age / THINKING_CYCLE_SECONDS) % 1.0
            gaze_x, gaze_y = _thinking_gaze(phase)
            expression = expression._replace(gaze_x=gaze_x, gaze_y=gaze_y)
            dy = -0.045 * F * _ease(breath)
            rotation = math.radians(3.0) * math.sin(math.tau * phase) ** 2
        elif look.idle_motion:
            dy = -0.004 * F * breath
    pose = (dy, rotation, 1.0)

    # Blend from what was drawn, including an interrupted transition or speech.
    if not still and age < POSE_BLEND_SECONDS:
        progress = _ease(age / POSE_BLEND_SECONDS)
        if state.transition_pose is not None:
            pose = tuple(old + (new - old) * progress for old, new in zip(state.transition_pose, pose))
        if state.transition_expression is not None:
            expression = Expression(*(old + (new - old) * progress
                                      for old, new in zip(state.transition_expression, expression)))
    state.body_pose = pose
    state.expression = expression

    cx, cy = width / 2, height / 2 - 0.08 * F
    cr.save()
    cr.translate(cx, cy + 0.63 * F)
    cr.scale(0.31 * F, 0.03 * F)
    cr.arc(0, 0, 1, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(0, 0, 0, 0.43)
    cr.fill()

    cr.save()
    cr.translate(cx, cy + pose[0])
    cr.rotate(pose[1])
    radius = F * min(0.5, look.roundness * 1.93 / 100.0)
    if look.enable_glow and expression.cue:
        colour = (0.898, 0.282, 0.302) if expression.cue < 0 else (1.0, 0.86, 0.45)
        for i in range(5, 0, -1):
            spread = 1.025 + 0.035 * i
            size = F * spread
            cr.set_source_rgba(*colour, 0.035 * abs(expression.cue))
            _rounded_rect(cr, -size / 2, -size / 2, size, size, radius * spread)
            cr.fill()

    cr.set_source_rgba(0, 0, 0, 0.30)
    _rounded_rect(cr, -F / 2, -F / 2 + F * 0.05, F, F, radius)
    cr.fill()
    cr.set_source_rgb(*look.body)
    _rounded_rect(cr, -F / 2, -F / 2, F, F, radius)
    cr.fill_preserve()
    cr.save()
    cr.clip()
    cr.set_source_rgba(0, 0, 0, 0.06)
    cr.rectangle(-F / 2, F / 2 - 0.02 * F, F, 0.02 * F)
    cr.fill()
    cr.restore()

    gaze_x, gaze_y = (0.0, 0.0) if still else (state.look_x, state.look_y)
    gx = max(-1.0, min(1.0, gaze_x + expression.gaze_x))
    gy = max(-1.0, min(1.0, gaze_y + expression.gaze_y))
    cr.translate(gx * 0.15 * F, gy * 0.11 * F)

    ew, eh = 0.115 * F * look.eye_size, 0.2 * F * look.eye_size
    eye_width = ew * expression.eye_width
    blink = 0.0 if still or mode == "sleeping" else state.blink
    center_y = -0.10 * eh + expression.eye_offset * F
    cr.set_source_rgb(*look.eye)
    for side in (-1, 1):
        open_height = eh * (expression.eye_height + side * expression.asymmetry)
        height = max(open_height * (1.0 - 0.92 * blink), 0.012 * F)
        x = side * (0.075 * F + ew / 2) - eye_width / 2
        _rounded_rect(cr, x, center_y - height / 2, eye_width, height, min(eye_width, height) / 2)
        cr.fill()
    cr.restore()


class Animator:
    """Gaze, blinks and amplitude over time: the per-frame state the face is drawn from."""

    def __init__(self, seed: int = 7):
        import random

        self.random = random.Random(seed)
        self.state = FaceState()
        self.last_step: float | None = None
        self.glance = (0.0, 0.0)
        self.next_glance = 0.0
        self.next_blink = 0.0
        self.blink_started = -math.inf
        self.envelope: list[float] = []
        self.envelope_hz = 50
        self.envelope_start = 0.0

    def set_mode(self, mode: str, now: float) -> None:
        if mode != self.state.mode:
            self.state.transition_pose = self.state.body_pose
            self.state.transition_expression = self.state.expression
            self.state.mode = mode
            self.state.mode_since = now

    def speak(self, envelope: list[float], hz: int, start: float) -> None:
        self.envelope, self.envelope_hz, self.envelope_start = list(envelope), int(hz or 50), start

    def needs_full_rate(self, now: float) -> bool:
        """Keep pose changes and brief blinks smooth at the resting frame rate."""
        if now - self.state.mode_since < POSE_BLEND_SECONDS:
            return True
        return self.state.mode != "sleeping" and (
            now >= self.next_blink or now - self.blink_started < BLINK_REOPEN_AT + BLINK_OPEN_SECONDS
        )

    def step(self, now: float, pointer: tuple[float, float] | None = None, idle_wander: bool = True) -> FaceState:
        s = self.state
        if self.last_step is None:
            self.next_glance = now + 1.0
            self.next_blink = now + 1.8
        dt = 0.0 if self.last_step is None else max(0.0, now - self.last_step)
        self.last_step = s.t = now

        # Hold a point of attention for a while, then settle into the next glance.
        target = (0.0, 0.0)
        if pointer is not None:
            target = (0.65 * pointer[0], 0.65 * pointer[1])
        elif idle_wander and s.mode != "sleeping":
            if now >= self.next_glance:
                self.glance = (self.random.uniform(-0.30, 0.30), self.random.uniform(-0.16, 0.16))
                self.next_glance = now + self.random.uniform(2.8, 5.8)
            target = self.glance
        gaze_blend = -math.expm1(-dt / 0.12)
        s.look_x += (target[0] - s.look_x) * gaze_blend
        s.look_y += (target[1] - s.look_y) * gaze_blend

        if now >= self.next_blink:
            self.blink_started = now
            self.next_blink = now + self.random.uniform(3.2, 7.5)
        blink_age = now - self.blink_started
        if blink_age < BLINK_CLOSE_SECONDS:
            s.blink = _ease(blink_age / BLINK_CLOSE_SECONDS)
        else:
            s.blink = 1.0 - _ease((blink_age - BLINK_REOPEN_AT) / BLINK_OPEN_SECONDS)

        s.amplitude = 0.0
        if self.envelope:
            index = math.floor((now - self.envelope_start) * self.envelope_hz)
            if 0 <= index < len(self.envelope):
                s.amplitude = self.envelope[index]
            if index >= len(self.envelope):
                self.envelope = []
        current = s.speech_motion or 0.0
        # Follow syllable attacks promptly and let the eyes ease open between sounds.
        time_constant = 0.030 if s.amplitude > current else 0.060
        s.speech_motion = current + (s.amplitude - current) * -math.expm1(-dt / time_constant)
        return s
