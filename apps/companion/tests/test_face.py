"""Echo's desktop face: every mode renders, matches the web proportions, no white halo."""
import cairo
import pytest

from echo_companion.face import MODES, Animator, Appearance, FaceState, canvas_size, draw, hex_rgb

F = 100


def render(state: FaceState, look: Appearance = Appearance()) -> cairo.ImageSurface:
    w, h = canvas_size(F)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    draw(cairo.Context(surface), w, h, F, state, look)
    return surface


def alpha_at(surface: cairo.ImageSurface, x: int, y: int) -> int:
    data = surface.get_data()
    return data[y * surface.get_stride() + x * 4 + 3]


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_renders_the_square_body(mode):
    s = render(FaceState(mode=mode, t=3.0, mode_since=2.5, amplitude=0.7))
    w, h = canvas_size(F)
    assert alpha_at(s, w // 2, int(h / 2 - 0.08 * F)) == 255  # the body is opaque at its centre


def test_no_white_halo_around_echo_in_normal_modes():
    for mode in ("idle", "speaking", "thinking", "working", "celebrating"):
        s = render(FaceState(mode=mode, t=1.0, mode_since=0.0, amplitude=1.0), Appearance(enable_glow=True))
        w, h = canvas_size(F)
        cy = int(h / 2 - 0.08 * F)
        # just outside the body's side edge there is nothing but transparency
        assert alpha_at(s, int(w / 2 + 0.5 * F + 0.08 * F), cy) == 0, mode


def test_error_keeps_its_red_cue():
    s = render(FaceState(mode="error", t=5.0, mode_since=0.0), Appearance(enable_glow=True))
    w, h = canvas_size(F)
    assert alpha_at(s, int(w / 2 + 0.5 * F + 0.06 * F), int(h / 2 - 0.08 * F)) > 0


def test_avatar_colours_are_read_like_the_web_config():
    look = Appearance.from_config({"body_color": "#f00", "eye_color": "#00ff00", "body_roundness": 99, "eye_size": 0.1})
    assert look.body == (1.0, 0.0, 0.0) and look.eye == (0.0, 1.0, 0.0)
    assert look.roundness == 40 and look.eye_size == 0.5
    assert hex_rgb("nonsense", (1, 1, 1)) == (1, 1, 1)


def test_speaking_follows_the_envelope_in_time():
    a = Animator()
    a.set_mode("speaking", 0.0)
    a.speak([0.0, 1.0, 0.0, 0.5], 10, start=100.0)
    assert a.step(100.05).amplitude == 0.0
    assert a.step(100.15).amplitude == 1.0
    assert a.step(100.35).amplitude == 0.5
    assert a.step(100.5).amplitude == 0.0 and a.envelope == []  # past the clip: silent, envelope released
    assert a.step(100.6).amplitude == 0.0
