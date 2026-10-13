"""Pixels through the real tool loop, scoped storage, and bounded input without moving the user's mouse."""
import base64
import io
import json
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from agent.lean import computer, observations, settings
from agent.lean.approvals import tool_needs_approval
from agent.lean.cloud_streams import _google_turns, anthropic_messages
from agent.lean.provider import ChatClient, Endpoint, ModelTurn, ProviderError, ToolCall
from agent.lean.toolbox import Toolbox
from config import config


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(observations, "root", lambda: tmp_path / "observations")


def capture(session="t", screen=None):
    return observations.save(Image.new("RGB", (1920, 1080), "red"), session, "Test screen", screen=screen)


def test_immutable_original_scoped_ownership_expiry_and_tampering():
    item = capture()
    _, data = observations.load(item["id"], "t")
    assert Image.open(io.BytesIO(data)).size == (1920, 1080)
    with pytest.raises(PermissionError):
        observations.load(item["id"], "other-chat")
    with pytest.raises(ValueError):
        observations.load("../../secret", "t")
    image = observations.root() / f"{item['id']}.png"
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        observations.load(item["id"], "t")
    item = capture()
    meta = observations.root() / f"{item['id']}.json"
    item["at"] = time.time() - observations.RETENTION - 1
    meta.write_text(json.dumps(item))
    with pytest.raises(FileNotFoundError):
        observations.load(item["id"], "t")
    assert not meta.exists()


def test_hydration_keeps_tool_results_together_and_bounds_images():
    messages = [{"role": "user", "content": "Look"}]
    for i in range(5):
        item = capture()
        messages.append({"role": "tool", "tool_call_id": str(i), "content": item["id"], "observations": [observations.reference(item)]})
    hydrated = observations.hydrate(messages)
    assert [m["role"] for m in hydrated] == ["user"] + ["tool"] * 5 + ["user"]
    images = [p for p in hydrated[-1]["content"] if p["type"] == "image_url"]
    assert len(images) == 3 and all("observations" not in m for m in hydrated)
    pixels = base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])
    assert Image.open(io.BytesIO(pixels)).size == (1280, 720)
    assert "data:image" not in json.dumps(messages)


def test_missing_observation_is_explicit_instead_of_hallucinated():
    hydrated = observations.hydrate([{"role": "tool", "content": "Screenshot", "observations": [{"id": "obs-" + "a" * 24, "session_id": "t"}]}])
    assert "unavailable or expired" in hydrated[-1]["content"][0]["text"]


def test_images_reach_claude_and_google_native_adapters():
    item = capture()
    hydrated = observations.hydrate([{"role": "tool", "tool_call_id": "c", "content": "Captured", "observations": [observations.reference(item)]}])
    _, claude = anthropic_messages(hydrated)
    assert claude[-1]["content"][-1]["type"] == "image"
    assert claude[-1]["content"][-1]["source"]["media_type"] == "image/png"
    google = _google_turns(hydrated)
    assert google[-1]["parts"][-1]["inlineData"]["mimeType"] == "image/png"


@pytest.mark.parametrize("provider,local", [("openai", False), ("gemini", False), ("xai", False), ("ollama", True), ("lmstudio", True)])
def test_chat_client_sends_pixels_on_openai_compatible_adapters(provider, local, monkeypatch):
    monkeypatch.setattr(config, "privacy_mode", "standard", raising=False)
    monkeypatch.setattr(config, "model_capability_profiles", {})
    from agent import privacy
    monkeypatch.setattr(privacy, "require", lambda *a: None)
    seen = []
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Red screen"},"finish_reason":"stop"}]}\n\n')
    client = ChatClient(Endpoint("http://127.0.0.1:3210/v1", "test-key", "vision-test", provider, local))
    client._http.close()
    client._http = httpx.Client(base_url=client.endpoint.base_url, transport=httpx.MockTransport(handle))
    try:
        item = capture()
        result = client.stream_turn([{"role": "tool", "tool_call_id": "c", "content": "Captured", "observations": [observations.reference(item)]}])
        assert result.content == "Red screen"
        assert seen[0]["messages"][-1]["content"][-1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "observations" not in json.dumps(seen[0])
    finally:
        client.close()


def test_text_only_profile_rejects_visual_input_clearly(monkeypatch):
    monkeypatch.setattr(config, "model_capability_profiles", {"ollama:text-test": {"vision": False}})
    client = ChatClient(Endpoint("http://127.0.0.1:3210/v1", "", "text-test", "ollama", True))
    try:
        with pytest.raises(ProviderError, match="text-only"):
            client.stream_turn([{"role": "user", "content": "look", "observations": [observations.reference(capture())]}])
    finally:
        client.close()


def test_real_loop_emits_chat_card_and_image_reference(monkeypatch):
    from agent.lean.loop import LeanTurn
    from agent.lean.personas import AgentPersona
    class ScriptedClient:
        def __init__(self, turns): self.turns, self.calls = turns, []
        def stream_turn(self, messages, **kwargs):
            self.calls.append([dict(m) for m in messages])
            result = self.turns.pop(0)
            if result.content and kwargs.get("on_content"): kwargs["on_content"](result.content)
            return result
    item = capture()
    monkeypatch.setattr(computer, "_grab", lambda session, args: item)
    box = Toolbox(toolsets=["vision"], extra_tools=computer.tools("t", threading.Event()), session_id="t")
    client = ScriptedClient([ModelTurn(tool_calls=[ToolCall("s", "take_screenshot", "{}")]), ModelTurn(content="I see a red screen.")])
    events = []
    result = LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="system", history=[], toolbox=box,
                      session_id="t", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(), persist_tool_runs=False).run("Show my screen")
    assert result.success
    assert client.calls[1][-1]["observations"] == [observations.reference(item)]
    assert any(event.get("widgets", [{}])[0].get("type") == "observation" for event in events if event.get("widgets"))
    assert "data:image" not in json.dumps(events)


def test_read_image_crops_original_and_keeps_control_space_separate():
    item = capture(screen={"left": 0, "top": 0, "width": 1920, "height": 1080})
    zoom = computer.read_image("t", {"observation_id": item["id"], "crop": [0, 0, 240, 120]})
    saved, _ = observations.load(zoom.observations[0]["id"], "t")
    assert (saved["width"], saved["height"], saved["screen"]) == (240, 120, None)
    with pytest.raises(ValueError, match="outside"):
        computer.read_image("t", {"observation_id": item["id"], "crop": [0, 0, 1921, 100]})


def test_read_project_image_respects_existing_file_boundaries(tmp_path):
    from agent.tools import _tool_execution_context
    project = tmp_path / "project"
    project.mkdir()
    target, outside = project / "example.png", tmp_path / "outside.png"
    Image.new("RGB", (20, 10), "blue").save(target)
    Image.new("RGB", (20, 10), "blue").save(outside)
    token = _tool_execution_context.set({"strict_scope": True, "project_root": str(project), "lean_runtime": True})
    try:
        assert computer.read_image("t", {"path": str(target)}).ok
        with pytest.raises(ValueError, match="allowed workspace"):
            computer.read_image("t", {"path": str(outside)})
    finally:
        _tool_execution_context.reset(token)


def test_mcp_images_are_decoded_and_bytes_removed_from_timeline():
    body = io.BytesIO()
    Image.new("RGB", (10, 10), "green").save(body, format="PNG")
    encoded = base64.b64encode(body.getvalue()).decode()
    block = {"type": "image", "mimeType": "image/png", "data": encoded}
    clean, images = observations.mcp_images(json.dumps({"content": [block], "structuredContent": {"image": block}}), "t", "mcp__screen")
    assert len(images) == 1 and encoded not in json.dumps(clean)
    assert observations.image_part(observations.reference(images[0]))["type"] == "image_url"
    assert observations.mcp_images("ordinary text", "t", "mcp__text") == ("ordinary text", [])


def test_coordinates_and_invalid_batches_are_checked_before_input():
    item = capture(screen={"left": -1920, "top": 100, "width": 1920, "height": 1080})
    actions = computer._validate([{"action": "click", "coordinate": [640, 360]}], item, ["w"])
    assert actions[0]["point"] == (-960, 640)
    for batch in ([{"action": "click", "coordinate": [1280, 0]}], [{"action": "hold", "text": "w", "duration": 10}], [{"action": "hold", "text": "fake"}], [{"action": "wait", "duration": 2}] * 3, [{"action": "wait"}] * 13):
        with pytest.raises(ValueError):
            computer._validate(batch, item, ["w"])


@pytest.fixture
def desktop(monkeypatch):
    import agent.tools as legacy
    monkeypatch.setattr(legacy, "_require_desktop_automation", lambda: None)
    bounds = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    class Capture:
        monitors = [{}, bounds]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Capture))
    events = []
    gui = SimpleNamespace(KEYBOARD_KEYS=["ctrl", "shift", "w"], FAILSAFE=True)
    for name in ("keyDown", "keyUp", "mouseDown", "mouseUp", "moveTo", "click", "scroll", "write", "failSafeCheck"):
        setattr(gui, name, lambda *args, _name=name, **kwargs: events.append((_name, args)))
    monkeypatch.setitem(sys.modules, "pyautogui", gui)
    # Every test must be incapable of injecting real relative desktop input.
    monkeypatch.setattr(computer, "_move_relative", lambda *delta: events.append(("relative", delta)))
    return capture(screen={**bounds, "monitor": 1}), gui, events


def test_cancel_releases_held_keys_and_restores_failsafe(desktop):
    item, gui, events = desktop
    cancel = threading.Event()
    def down(key):
        events.append(("keyDown", (key,)))
        cancel.set()
    gui.keyDown = down
    with pytest.raises(ValueError, match="cancelled"):
        computer.act("t", {"observation_id": item["id"], "actions": [{"action": "hold", "text": "ctrl+w", "duration": 2}]}, cancel)
    assert ("keyUp", ("ctrl",)) in events and ("keyDown", ("w",)) not in events
    assert gui.FAILSAFE and not computer._desktop_lock.locked()


def test_drag_failure_releases_mouse(desktop):
    item, gui, events = desktop
    def move(*args, **kwargs):
        if kwargs.get("duration"):
            raise RuntimeError("failsafe")
    gui.moveTo = move
    with pytest.raises(RuntimeError, match="failsafe"):
        computer.act("t", {"observation_id": item["id"], "actions": [{"action": "drag", "start_coordinate": [10, 10], "coordinate": [20, 20]}]})
    assert ("mouseUp", ()) in events and gui.FAILSAFE and not computer._desktop_lock.locked()


def test_successful_input_captures_result_and_invalid_or_stale_input_never_runs(desktop, monkeypatch):
    item, gui, events = desktop
    monkeypatch.setattr(computer, "_grab", lambda session, args: capture(screen=item["screen"]))
    result = computer.act("t", {"observation_id": item["id"], "actions": [{"action": "click", "coordinate": [640, 360]}]})
    assert result.ok and result.observations[0]["id"] != item["id"]
    assert events == [("click", (960, 540))]
    events.clear()
    with pytest.raises(ValueError):
        computer.act("t", {"observation_id": item["id"], "actions": [{"action": "key", "text": "w"}, {"action": "click", "coordinate": [99999, 0]}]})
    assert events == []
    monkeypatch.setattr(computer.time, "time", lambda: item["at"] + 121)
    with pytest.raises(ValueError, match="fresh"):
        computer.act("t", {"observation_id": item["id"], "actions": [{"action": "key", "text": "w"}]})
    assert events == []


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -float("inf"), True, "1", 3, 10 ** 500])
@pytest.mark.parametrize("kind", ["key", "hold", "wait", "drag"])
def test_all_timed_actions_validate_before_any_input(desktop, duration, kind):
    item, _, events = desktop
    action = {"action": kind, "text": "w", "duration": duration,
              "coordinate": [20, 20], "start_coordinate": [10, 10]}
    with pytest.raises(ValueError, match="finite number"):
        computer.act("t", {"observation_id": item["id"], "actions": [
            {"action": "click", "coordinate": [10, 10]}, action]})
    assert events == []


@pytest.mark.parametrize("action", [
    {"action": "move_relative", "delta": [1001, 0]},
    {"action": "move_relative", "delta": [0, -1001]},
    {"action": "move_relative", "delta": [True, 0]},
    {"action": "move_relative", "delta": [0.5, 0]},
    {"action": "move_relative", "delta": [0]},
    {"action": "hold", "button": "wheel"},
    {"action": "hold", "button": {"name": "left"}},
    {"action": "hold"},
    {"action": "hold", "text": "w+w"},
])
def test_relative_motion_and_mouse_hold_reject_invalid_batches(desktop, action):
    item, _, events = desktop
    with pytest.raises(ValueError):
        computer.act("t", {"observation_id": item["id"], "actions": [
            {"action": "key", "text": "w"}, action]})
    assert events == []


def test_combined_mouse_keyboard_hold_releases_before_recapture(desktop, monkeypatch):
    item, gui, events = desktop
    gui.mouseDown = lambda **kw: events.append(("mouseDown", kw["button"]))
    gui.mouseUp = lambda **kw: events.append(("mouseUp", kw["button"]))
    waits = []
    cancel = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: waits.append(seconds) or False)
    def grab(*args):
        assert events == [("keyDown", ("shift",)), ("keyDown", ("w",)),
                          ("mouseDown", "left"), ("mouseUp", "left"),
                          ("keyUp", ("w",)), ("keyUp", ("shift",))]
        return capture(screen=item["screen"])
    monkeypatch.setattr(computer, "_grab", grab)
    result = computer.act("t", {"observation_id": item["id"], "actions": [
        {"action": "hold", "text": "shift+w", "button": "left", "duration": 0.75,
         "point": [99999, 99999]}]}, cancel)
    assert result.ok and waits == [0.75, 0.1]
    assert gui.FAILSAFE and not computer._desktop_lock.locked()


def test_cancelling_mouse_hold_releases_mouse_and_keys(desktop):
    item, gui, events = desktop
    cancel = threading.Event()
    def down(**kwargs):
        events.append(("mouseDown", kwargs["button"]))
        cancel.set()
    gui.mouseDown = down
    gui.mouseUp = lambda **kw: events.append(("mouseUp", kw["button"]))
    with pytest.raises(ValueError, match="cancelled"):
        computer.act("t", {"observation_id": item["id"], "actions": [
            {"action": "hold", "text": "w", "button": "right", "duration": 2}]}, cancel)
    assert ("mouseUp", "right") in events and ("keyUp", ("w",)) in events
    assert gui.FAILSAFE and not computer._desktop_lock.locked()


def test_mouse_release_failure_is_retried_during_cleanup(desktop):
    item, gui, events = desktop
    def up(**kwargs):
        events.append(("mouseUp", kwargs["button"]))
        if events.count(("mouseUp", "middle")) == 1:
            raise RuntimeError("release failed")
    gui.mouseUp = up
    with pytest.raises(RuntimeError, match="release failed"):
        computer.act("t", {"observation_id": item["id"], "actions": [
            {"action": "hold", "text": "w", "button": "middle", "duration": 0}]})
    assert events.count(("mouseUp", "middle")) == 2
    assert ("keyUp", ("w",)) in events and gui.FAILSAFE and not computer._desktop_lock.locked()


def test_relative_motion_keeps_signed_deltas_and_honors_failsafe(desktop, monkeypatch):
    item, gui, events = desktop
    monkeypatch.setattr(computer, "_grab", lambda *args: capture(screen=item["screen"]))
    result = computer.act("t", {"observation_id": item["id"], "actions": [
        {"action": "move_relative", "delta": [-40, 25]}]})
    assert result.ok and events == [("failSafeCheck", ()), ("relative", (-40, 25))]
    events.clear()
    def fail(): raise RuntimeError("failsafe")
    gui.failSafeCheck = fail
    with pytest.raises(RuntimeError, match="failsafe"):
        computer.act("t", {"observation_id": item["id"], "actions": [
            {"action": "move_relative", "delta": [40, -25]}]})
    assert events == [] and gui.FAILSAFE and not computer._desktop_lock.locked()


def test_win32_relative_motion_uses_correct_sendinput_layout_without_injecting(monkeypatch):
    import ctypes
    seen = []
    def send(count, pointer, size):
        event = pointer._obj
        seen.append((count, size, event.type, event.mi.dx, event.mi.dy,
                     event.mi.dwFlags, event.mi.mouseData, event.mi.dwExtraInfo))
        return 1
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kw: SimpleNamespace(SendInput=send), raising=False)
    computer._move_relative(-123, 456)
    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert seen == [(1, expected_size, 0, -123, 456, 1, 0, 0)]
    send.argtypes  # The native call declares its pointer/width ABI.
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kw: SimpleNamespace(SendInput=lambda *a: 0), raising=False)
    with pytest.raises(OSError, match="rejected relative mouse input"):
        computer._move_relative(1, 2)


def test_desktop_actions_still_need_permission_and_approval(monkeypatch):
    import agent.tools as legacy
    monkeypatch.setattr(legacy, "_require_desktop_automation", lambda: "Desktop control is disabled")
    with pytest.raises(ValueError, match="disabled"):
        computer.act("t", {"observation_id": capture()["id"], "actions": []})
    for mode in ("smart", "always"):
        monkeypatch.setattr(settings, "approval_mode", lambda: mode)
        assert tool_needs_approval(None, "computer_action", {})[0]


def test_authenticated_observation_endpoint_never_accepts_arbitrary_paths():
    from api.routes.lean import observation_image
    from fastapi import HTTPException
    item = capture()
    assert observation_image(item["id"], "t").media_type == "image/png"
    with pytest.raises(HTTPException) as exc:
        observation_image(item["id"], "other-chat")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        observation_image("../config", "t")
    assert exc.value.status_code == 404


def test_followup_history_preserves_image_references_and_policy_taint(monkeypatch):
    import agent.state as state
    from agent.lean import summaries
    from agent.lean.loop import LeanTurn
    from agent.lean.personas import AgentPersona
    from agent.lean.runtime import LeanSession
    item = capture()
    timeline = {"turns": [{"execution_id": "old", "messages": [
        {"role": "user", "text": "Look at my screen"},
        {"role": "assistant", "text": "", "timeline": [{"kind": "tool", "widgets": [{"type": "observation", "data": observations.public(item)}]}]},
    ]}]}
    monkeypatch.setattr(state, "get_state_store", lambda: SimpleNamespace(session_timeline=lambda *a, **k: timeline))
    monkeypatch.setattr(summaries, "load", lambda _: {})
    session = LeanSession.__new__(LeanSession)
    session.session_id, session.room = "t", None
    session.caller_role = "owner"
    history = session._history()
    assert history[-1]["observations"] == [observations.reference(item)]
    assert observations.hydrate(history)[-1]["content"][-1]["type"] == "image_url"
    turn = LeanTurn(client=None, persona=AgentPersona(id="echo", name="Echo"), system_prompt="system", history=history,
                    toolbox=Toolbox(toolsets=["vision"], session_id="t"), session_id="t", request_id="r", execution_id="e", emit=lambda _: None, cancel=threading.Event())
    assert "read_image" in turn.taint
    session.caller_role = "public"
    assert not any(row.get("observations") for row in session._history())


def test_mcp_registry_execution_propagates_image_references(monkeypatch):
    body = io.BytesIO()
    Image.new("RGB", (20, 10)).save(body, format="PNG")
    encoded = base64.b64encode(body.getvalue()).decode()
    box = Toolbox(toolsets=["none"], session_id="t")
    box.entries["mcp__camera"] = SimpleNamespace(origin="mcp", func=lambda: json.dumps({"content": [{"type": "image", "mimeType": "image/png", "data": encoded}]}))
    result = box.run("mcp__camera", {})
    assert result.ok and result.widgets[0]["type"] == "observation" and result.observations
    assert encoded not in result.output


def test_gemini_live_continuation_receives_pixels_before_tool_completion(monkeypatch):
    import websockets.sync.client
    from agent import privacy
    monkeypatch.setattr(privacy, "require", lambda *a: None)
    monkeypatch.setattr(config, "model_capability_profiles", {})
    class Socket:
        def __init__(self):
            self.packets = iter([{"setupComplete": {}}, {"toolCall": {"functionCalls": [{"id": "c", "name": "take_screenshot", "args": {}}]}}, {"serverContent": {"outputTranscription": {"text": "Red"}, "turnComplete": True}}])
            self.sent = []
        def send(self, message): self.sent.append(json.loads(message))
        def recv(self, timeout): return json.dumps(next(self.packets))
        def close(self): pass
    socket = Socket()
    monkeypatch.setattr(websockets.sync.client, "connect", lambda *a, **k: socket)
    client = ChatClient(Endpoint("https://generativelanguage.googleapis.com", "test-key", "gemini-3.8-live", "gemini", False))
    try:
        assert client.stream_turn([{"role": "user", "content": "Look"}]).tool_calls
        item = capture()
        assert client.stream_turn([{"role": "tool", "tool_call_id": "c", "content": "Captured", "observations": [observations.reference(item)]}]).content == "Red"
        assert socket.sent[-2]["clientContent"]["turns"][0]["parts"][-1]["inlineData"]["mimeType"] == "image/png"
        assert socket.sent[-1]["toolResponse"]["functionResponses"][0]["id"] == "c"
    finally:
        client.close()


def test_history_images_join_the_next_user_message_for_strict_templates():
    # Gemma-style chat templates reject two user messages in a row.
    item = capture()
    out = observations.hydrate([
        {"role": "user", "content": "look"},
        {"role": "assistant", "content": "Saw it", "observations": [observations.reference(item)]},
        {"role": "user", "content": "now click"}])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[-1]["content"][1]["type"] == "image_url"
    assert out[-1]["content"][-1] == {"type": "text", "text": "now click"}


def test_model_that_rejects_images_gets_text_notes_instead_of_a_failed_turn(monkeypatch):
    monkeypatch.setattr(config, "model_capability_profiles", {})
    from agent import privacy
    monkeypatch.setattr(privacy, "require", lambda *a: None)
    seen = []
    def handle(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(400, json={"error": "Model does not support images. Please use a model that does."})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"I cannot see it"},"finish_reason":"stop"}]}\n\n')
    client = ChatClient(Endpoint("http://127.0.0.1:1234/v1", "", "text-model", "lmstudio", True))
    client._http.close()
    client._http = httpx.Client(base_url=client.endpoint.base_url, transport=httpx.MockTransport(handle))
    turn = [{"role": "tool", "tool_call_id": "c", "content": "Captured", "observations": [observations.reference(capture())]}]
    try:
        assert client.stream_turn(turn).content == "I cannot see it"
        assert len(seen) == 2 and "image_url" not in json.dumps(seen[1])
        assert "does not accept images" in json.dumps(seen[1])
        client.stream_turn(turn)  # later steps skip the request that will fail
        assert len(seen) == 3 and "image_url" not in json.dumps(seen[2])
    finally:
        client.close()


def test_screenshots_fit_what_providers_show_the_model():
    # Neither Claude (~1.15 MP) nor OpenAI (768 px short side) rescales again,
    # so the model's coordinates are the ones computer_action maps to the screen.
    for size in [(1920, 1080), (2560, 1600), (3440, 1440), (1600, 1200)]:
        item = observations.save(Image.new("RGB", size, "red"), "t", "Screen")
        w, h = item["model_width"], item["model_height"]
        assert w * h <= 1_150_000 and min(w, h) <= 768 and max(w, h) <= 1568


def test_game_keys_carry_scan_codes(monkeypatch):
    sent = []
    class User32:
        def MapVirtualKeyW(self, code, kind): return {0x57: 0x11, 0x26: 0x48}[code]
        def keybd_event(self, vk, scan, flags, extra): sent.append((vk, scan, flags))
    import ctypes
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: User32(), raising=False)
    monkeypatch.setattr(computer.os, "name", "nt")
    fallback = []
    gui = SimpleNamespace(platformModule=SimpleNamespace(keyboardMapping={"w": 0x57, "up": 0x26, "!": 0x131}),
                          isShiftCharacter=lambda key: key == "!",
                          keyDown=lambda key: fallback.append(("down", key)), keyUp=lambda key: fallback.append(("up", key)))
    computer._key(gui, "w", True)
    computer._key(gui, "w", False)
    computer._key(gui, "up", True)
    computer._key(gui, "!", True)
    assert sent == [(0x57, 0x11, 0), (0x57, 0x11, 2), (0x26, 0x48, 1)]
    assert fallback == [("down", "!")]
