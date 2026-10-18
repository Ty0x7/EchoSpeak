"""Screen observations and bounded desktop actions, governed by the existing tool loop."""
from __future__ import annotations

import io
import math
import os
import threading
import time
from typing import Any

from PIL import Image

from agent.lean import observations

_desktop_lock = threading.Lock()
# Each chat's newest screen capture: native computer tools (Claude's toolset) act on
# "the screen" rather than naming a capture, so their coordinates mean this one.
_current: dict[str, str] = {}

# Timed actions. A game needs a few seconds of W to walk anywhere; the batch cap
# keeps every batch short enough to observe and stop.
MAX_ACTION_SECONDS = 5
MAX_BATCH_SECONDS = 10


def _result(item: dict, detail: str = ""):
    from agent.lean.toolbox import ToolResult
    if item.get("screen"):
        _current[item["session_id"]] = item["id"]
    return ToolResult(True, (f"{detail}\nScreenshot {item['id']}: {item['source']}. "
        f"Image attached. Coordinate space: {item['model_width']}x{item['model_height']}. "
        "Use read_image with this ID to revisit it. Capture again after the screen changes.").strip(), 0,
        widgets=[{"type": "observation", "data": observations.public(item)}],
        observations=[observations.reference(item)])


def _grab(session: str, args: dict):
    import mss
    screen: dict[str, Any]
    window = str(args.get("window_title") or "").strip()
    with mss.mss() as capture:
        if window or args.get("window_handle"):
            from pywinauto import Desktop
            wins = [w for w in Desktop(backend="uia").windows() if w.is_visible() and
                    (int(w.handle) == args["window_handle"] if args.get("window_handle") else window.casefold() in w.window_text().casefold())]
            if len(wins) != 1:
                raise ValueError("Name one visible window unambiguously; use desktop_list_windows first")
            win = wins[0]
            rect = win.rectangle()
            screen = {"left": rect.left, "top": rect.top, "width": rect.width(), "height": rect.height(),
                      "window": int(win.handle)}
            source = win.window_text()
        else:
            monitor = int(args.get("monitor", 1))
            if monitor < 1 or monitor >= len(capture.monitors):
                raise ValueError("Monitor unavailable; monitor numbers start at 1")
            screen = dict(capture.monitors[monitor])
            screen["monitor"] = monitor
            source = f"Monitor {monitor}"
        region = {k: screen[k] for k in ("left", "top", "width", "height")}
        if region["width"] <= 0 or region["height"] <= 0 or region["width"] * region["height"] > observations.MAX_PIXELS:
            raise ValueError("Window cannot be captured at this size")
        raw = capture.grab(region)
        image = Image.frombytes("RGB", raw.size, raw.rgb)
    return observations.save(image, session, source, screen=screen)


def screenshot(session: str, args: dict):
    if not _desktop_lock.acquire(timeout=2):
        raise ValueError("Desktop is busy with another action; try again after it finishes")
    try:
        item = _grab(session, args)
        if args.get("path"):
            from agent.tools import _safe_file_path
            target = _safe_file_path(str(args["path"]))
            if target is None or target.suffix.lower() != ".png":
                raise ValueError("Export requires an allowed workspace PNG path")
            _, body = observations.load(item["id"], session)
            target.write_bytes(body)
        return _result(item)
    finally:
        _desktop_lock.release()


def read_image(session: str, args: dict):
    if args.get("observation_id"):
        item, data = observations.load(str(args["observation_id"]), session)
    elif args.get("path"):
        from agent.tools import _safe_file_path
        target = _safe_file_path(str(args["path"]))
        if target is None or not target.is_file():
            raise ValueError("Image must be a file in an allowed workspace; use an observation ID for screenshots")
        if target.stat().st_size > observations.MAX_BYTES:
            raise ValueError("Image exceeds 20 MB")
        data = target.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            item = observations.save(image, session, target.name)
    else:
        raise ValueError("Provide observation_id or path")
    crop = args.get("crop")
    if crop is not None:
        if not isinstance(crop, list) or len(crop) != 4 or not all(type(v) is int for v in crop):
            raise ValueError("crop must be [left, top, right, bottom] in original image pixels")
        x0, y0, x1, y1 = crop
        if not (0 <= x0 < x1 <= item["width"] and 0 <= y0 < y1 <= item["height"]):
            raise ValueError("Crop is outside the original image")
        with Image.open(io.BytesIO(data)) as image:
            # Zoom is for reading only; never silently changes a control coordinate space.
            item = observations.save(image.crop(tuple(crop)), session, f"Zoom of {item['id']}")
    return _result(item)


def _point(action: dict, name: str, item: dict) -> tuple[int, int]:
    value = action.get(name)
    if not isinstance(value, list) or len(value) != 2 or not all(type(v) is int for v in value):
        raise ValueError(f"{name} must be [x, y] in screenshot pixels")
    x, y = value
    if not (0 <= x < item["model_width"] and 0 <= y < item["model_height"]):
        raise ValueError("Coordinate outside screenshot; capture again instead of guessing")
    s = item["screen"]
    return (s["left"] + int(x * s["width"] / item["model_width"]),
            s["top"] + int(y * s["height"] / item["model_height"]))


def _move_relative(dx: int, dy: int) -> None:
    """Send relative motion, preserving pointer-lock camera input without warping.

    Win32 MOUSEINPUT uses MOUSEEVENTF_MOVE without MOUSEEVENTF_ABSOLUTE.
    Windows pointer acceleration and the target's sensitivity still apply.
    https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-mouseinput
    """
    import ctypes

    # Fixed-width Win32 fields, with a pointer-sized ULONG_PTR, work on x86/x64.
    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                    ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]

    class InputUnion(ctypes.Union):
        # MOUSEINPUT is the largest member of Win32's INPUT union.
        _fields_ = [("mi", MouseInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", ctypes.c_uint32), ("data", InputUnion)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send = user32.SendInput
    send.argtypes = (ctypes.c_uint32, ctypes.POINTER(Input), ctypes.c_int)
    send.restype = ctypes.c_uint32
    event = Input(type=0, data=InputUnion(mi=MouseInput(dx=dx, dy=dy, dwFlags=0x0001)))
    if send(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
        # UIPI can block injection without setting a meaningful GetLastError.
        raise OSError("Windows rejected relative mouse input. Check the target's permissions and input support.")


# Navigation keys, Windows keys, numpad divide, Num Lock and the right-hand Ctrl/Alt.
_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5}


def _key(gui, key: str, down: bool) -> None:
    """Press or release a key with its hardware scan code as well as its virtual key.

    pyautogui sends scan code 0, which games reading scan codes (DirectInput and
    many raw-input engines) ignore. Characters that need Shift keep pyautogui's path.
    """
    code = getattr(getattr(gui, "platformModule", None), "keyboardMapping", {}).get(key) if os.name == "nt" else None
    if not code or code >= 0x100 or gui.isShiftCharacter(key):
        (gui.keyDown if down else gui.keyUp)(key)
        return
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    scan = user32.MapVirtualKeyW(code, 0)  # MAPVK_VK_TO_VSC
    flags = (0x0001 if code in _EXTENDED_VK else 0) | (0 if down else 0x0002)  # EXTENDEDKEY, KEYUP
    user32.keybd_event(code, scan, flags, 0)


def _validate(actions: Any, item: dict, keyboard_keys: list[str]) -> list[dict]:
    if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
        raise ValueError("Provide 1–12 actions; observe again between batches")
    prepared, seconds = [], 0.0
    allowed = {"click", "double_click", "triple_click", "right_click", "move", "move_relative", "drag", "scroll", "type", "key", "hold", "wait"}
    for raw in actions:
        if not isinstance(raw, dict) or raw.get("action") not in allowed:
            raise ValueError("Unknown computer action")
        # Never trust caller-supplied internal/prepared coordinates or keys.
        a = {key: value for key, value in raw.items() if key not in {"point", "start", "keys"}}
        kind = a["action"]
        if kind in {"click", "double_click", "triple_click", "right_click", "move", "drag", "scroll"}:
            a["point"] = _point(a, "coordinate", item)
        if kind == "drag":
            a["start"] = _point(a, "start_coordinate", item)
        if kind == "move_relative":
            delta = a.get("delta")
            if not isinstance(delta, list) or len(delta) != 2 or not all(type(v) is int and -1000 <= v <= 1000 for v in delta):
                raise ValueError("delta must be [dx, dy] with integers between -1000 and 1000")
        if kind in {"click", "double_click", "triple_click", "right_click", "drag"} or (kind == "hold" and "button" in a):
            button = a.get("button", "right" if kind == "right_click" else "left")
            if button not in ("left", "right", "middle") or (kind == "right_click" and button != "right"):
                raise ValueError("button must be left, right or middle; right_click uses right")
            a["button"] = button
        if kind == "hold" and "coordinate" in a:
            a["point"] = _point(a, "coordinate", item)
        if kind in {"hold", "wait", "drag", "key"}:
            duration = a.get("duration", 0.05 if kind == "key" else 0.3)
            if type(duration) not in (float, int) or not 0 <= duration <= MAX_ACTION_SECONDS or not math.isfinite(duration):
                raise ValueError(f"Each duration must be a finite number between 0 and {MAX_ACTION_SECONDS} seconds")
            seconds += duration
            a["duration"] = duration
        if kind in {"key", "hold"}:
            text = a.get("text", "")
            if not isinstance(text, str):
                raise ValueError("Key text must be a string such as w or shift+w")
            keys = [k.strip() for k in text.lower().split("+")] if text else []
            if len(keys) > 5 or any(k not in keyboard_keys for k in keys):
                raise ValueError("Unknown key; use names such as ctrl+s, enter, space, w or shift+w")
            if not keys and (kind == "key" or "button" not in a):
                raise ValueError("Provide key text or, for hold, a mouse button")
            if len(set(keys)) != len(keys):
                raise ValueError("Do not repeat a held key")
            a["keys"] = keys
        if kind == "type":
            text = str(a.get("text") or "")
            if len(text) > 2000 or not text.isascii():
                raise ValueError("Type accepts up to 2000 ASCII characters; use desktop_type_text for Unicode")
            a["text"] = text
        if kind == "scroll":
            amount = a.get("amount", 0)
            if type(amount) is not int or not -20 <= amount <= 20:
                raise ValueError("Scroll amount must be an integer between -20 and 20")
            if not isinstance(a.get("horizontal", False), bool):
                raise ValueError("horizontal must be true or false")
        prepared.append(a)
    if seconds > MAX_BATCH_SECONDS:
        raise ValueError(f"Action batches are limited to {MAX_BATCH_SECONDS} seconds; observe before continuing")
    return prepared


def act(session: str, args: dict, cancel: threading.Event | None = None):
    from agent.tools import _require_desktop_automation
    if error := _require_desktop_automation():
        raise ValueError(error)
    item, _ = observations.load(str(args.get("observation_id") or ""), session)
    if not item.get("screen") or time.time() - item["at"] > 120:
        raise ValueError("A fresh screen capture from the last two minutes is required")
    import pyautogui as gui
    prepared = _validate(args.get("actions"), item, gui.KEYBOARD_KEYS)
    cancel = cancel or threading.Event()
    if not _desktop_lock.acquire(timeout=2):
        raise ValueError("Desktop is busy with another action")
    held, mouse = set(), set()
    try:
        screen = item["screen"]
        if cancel.is_set():
            raise ValueError("Computer action cancelled")
        if screen.get("window"):
            from pywinauto import Desktop
            win = Desktop(backend="uia").window(handle=screen["window"])
            rect = win.rectangle()
            if (rect.left, rect.top, rect.width(), rect.height()) != tuple(screen[k] for k in ("left", "top", "width", "height")):
                raise ValueError("Window moved or resized; capture it again before acting")
            win.set_focus()
        else:
            import mss
            with mss.mss() as capture:
                monitor = screen["monitor"]
                if monitor >= len(capture.monitors) or any(capture.monitors[monitor][k] != screen[k] for k in ("left", "top", "width", "height")):
                    raise ValueError("Monitor layout changed; capture again before acting")
        for a in prepared:
            if cancel.is_set():
                raise ValueError("Computer action cancelled")
            kind = a["action"]
            if kind in {"click", "double_click", "triple_click", "right_click"}:
                gui.click(*a["point"], clicks={"double_click": 2, "triple_click": 3}.get(kind, 1),
                          button=a["button"], interval=0.1)
            elif kind == "move":
                gui.moveTo(*a["point"])
            elif kind == "move_relative":
                gui.failSafeCheck()
                _move_relative(*a["delta"])
            elif kind == "scroll":
                gui.moveTo(*a["point"])
                (gui.hscroll if a.get("horizontal") else gui.scroll)(a.get("amount", 0))
            elif kind == "drag":
                gui.moveTo(*a["start"])
                mouse.add(a["button"])
                gui.mouseDown(button=a["button"])
                gui.moveTo(*a["point"], duration=a["duration"])
                gui.mouseUp(button=a["button"])
                mouse.discard(a["button"])
            elif kind == "type":
                gui.write(a["text"])
            elif kind in {"key", "hold"}:
                if "point" in a:
                    gui.moveTo(*a["point"])
                for key in a["keys"]:
                    if cancel.is_set():
                        raise ValueError("Computer action cancelled")
                    held.add(key)
                    _key(gui, key, True)
                if cancel.is_set():
                    raise ValueError("Computer action cancelled")
                if kind == "hold" and "button" in a:
                    mouse.add(a["button"])
                    gui.mouseDown(button=a["button"])
                if cancel.wait(a["duration"]):
                    raise ValueError("Computer action cancelled")
                if kind == "hold" and "button" in a:
                    gui.mouseUp(button=a["button"])
                    mouse.discard(a["button"])
                for key in reversed(a["keys"]):
                    _key(gui, key, False)
                    held.discard(key)
            elif cancel.wait(a["duration"]):
                raise ValueError("Computer action cancelled")
        # Give the target a render tick after immediate SendInput before observing.
        if cancel.wait(0.1):
            raise ValueError("Computer action cancelled")
        # Always observe the actual result, including games and non-accessible canvas UIs.
        next_args = {"monitor": screen.get("monitor", 1)}
        if screen.get("window"):
            next_args = {"window_handle": screen["window"]}
        return _result(_grab(session, next_args), f"Executed {len(prepared)} actions. Inspect the resulting screen.")
    finally:
        # The user's failsafe corner must stop actions without preventing key release.
        previous = gui.FAILSAFE
        gui.FAILSAFE = False
        try:
            for key in held:
                try:
                    _key(gui, key, False)
                except Exception:
                    pass
            for button in mouse:
                try:
                    gui.mouseUp(button=button)
                except Exception:
                    pass
        finally:
            gui.FAILSAFE = previous
            _desktop_lock.release()


# ── Claude's native computer toolset (computer_toolset_20260801) ─────────
# Claude is trained on its own action names; on Anthropic models with the toolset
# they replace computer_action (agent/lean/cloud_streams.py). Each one runs through
# act() above, so permissions, approvals, fresh-capture and geometry checks apply.
from agent.lean.policy import NATIVE_COMPUTER_ACT, NATIVE_COMPUTER_LOOK  # noqa: E402  (classified with the policy)

# Raw button down/up would leave a button pressed between model steps; left_click_drag covers it.
NATIVE_OFF = {"left_mouse_down", "left_mouse_up"}
NATIVE_MEMBERS = NATIVE_COMPUTER_LOOK | NATIVE_COMPUTER_ACT

# xdotool-style names Claude uses, as pyautogui names.
_KEY_NAMES = {"return": "enter", "kp_enter": "enter", "escape": "esc", "page_down": "pagedown", "next": "pagedown",
              "page_up": "pageup", "prior": "pageup", "super": "win", "super_l": "win", "cmd": "win", "meta": "win",
              "control": "ctrl", "control_l": "ctrl", "control_r": "ctrlright", "alt_l": "alt", "alt_r": "altright",
              "shift_l": "shift", "shift_r": "shiftright", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
              "arrowright": "right", "caps_lock": "capslock", "num_lock": "numlock", "print": "printscreen", "minus": "-",
              "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/", "backslash": "\\", "semicolon": ";",
              "apostrophe": "'", "grave": "`", "bracketleft": "[", "bracketright": "]"}


def _keys(text: Any) -> str:
    parts = [p.strip() for p in str(text or "").split("+") if p.strip()]
    return "+".join(_KEY_NAMES.get(p.lower(), p.lower()) for p in parts)


def _current_item(session: str) -> dict:
    ref = _current.get(session)
    if not ref:
        raise ValueError("Take a screenshot first; coordinates refer to the latest screenshot")
    item, _ = observations.load(ref, session)
    return item


def _cursor(item: dict) -> list[int]:
    import pyautogui as gui
    x, y = gui.position()
    s = item["screen"]
    mx = int((x - s["left"]) * item["model_width"] / s["width"])
    my = int((y - s["top"]) * item["model_height"] / s["height"])
    if not (0 <= mx < item["model_width"] and 0 <= my < item["model_height"]):
        raise ValueError("The pointer is outside the captured screen; pass a coordinate")
    return [mx, my]


def _native_actions(name: str, args: dict, item: dict) -> list[dict]:
    """One native call as computer_action steps."""
    coordinate = args.get("coordinate")
    if name in {"left_click", "right_click", "middle_click", "double_click", "triple_click"}:
        point = coordinate if coordinate is not None else _cursor(item)
        button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
        if args.get("text"):  # modifier keys held during a single click, e.g. shift-click
            if name in {"double_click", "triple_click"}:
                raise ValueError("Modifier keys work with single clicks only")
            return [{"action": "hold", "text": _keys(args["text"]), "button": button, "coordinate": point, "duration": 0.05}]
        kind = {"left_click": "click", "middle_click": "click"}.get(name, name)
        return [{"action": kind, "coordinate": point, **({"button": button} if kind == "click" else {})}]
    if name == "left_click_drag":
        return [{"action": "drag", "start_coordinate": args.get("start_coordinate"), "coordinate": coordinate}]
    if name == "mouse_move":
        return [{"action": "move", "coordinate": coordinate}]
    if name == "scroll":
        direction = str(args.get("scroll_direction") or "down")
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("scroll_direction must be up, down, left or right")
        if args.get("text"):
            raise ValueError("Scrolling with modifier keys is not supported here; scroll without them")
        amount = min(20, max(1, int(args.get("scroll_amount") or 3)))
        return [{"action": "scroll", "coordinate": coordinate if coordinate is not None else _cursor(item),
                 "amount": amount if direction in {"up", "right"} else -amount, "horizontal": direction in {"left", "right"}}]
    if name == "type":
        return [{"action": "type", "text": str(args.get("text") or "")}]
    if name == "key":
        repeat = int(args.get("repeat") or 1)
        if not 1 <= repeat <= 12:
            raise ValueError("repeat must be 1-12 here; send more in another call")
        return [{"action": "key", "text": _keys(args.get("text"))}] * repeat
    if name == "hold_key":
        duration = args.get("duration", 1)
        if type(duration) in (int, float) and duration > MAX_ACTION_SECONDS:
            raise ValueError(f"Holds are limited to {MAX_ACTION_SECONDS} seconds here; hold again to keep going")
        return [{"action": "hold", "text": _keys(args.get("text")), "duration": duration}]
    raise ValueError(f"{name} is not available here")


def native(session: str, name: str, args: dict, cancel: threading.Event):
    """Run one member of Claude's computer toolset."""
    from agent.lean.toolbox import ToolResult
    if name in NATIVE_OFF:
        raise ValueError("Pressing and releasing a button in separate steps is off here; use left_click_drag")
    if name == "screenshot":
        last = _current.get(session)
        target: dict = {"monitor": 1}
        if last:
            try:
                screen = observations.load(last, session)[0].get("screen") or {}
                target = {"window_handle": screen["window"]} if screen.get("window") else {"monitor": screen.get("monitor", 1)}
            except (OSError, ValueError, KeyError, PermissionError):
                pass
        return screenshot(session, target)
    item = _current_item(session)
    if name == "zoom":
        region = args.get("region")
        if not isinstance(region, list) or len(region) != 4 or not all(type(v) is int for v in region):
            raise ValueError("region must be [x0, y0, x1, y1] in screenshot pixels")
        sx, sy = item["width"] / item["model_width"], item["height"] / item["model_height"]
        crop = [int(region[0] * sx), int(region[1] * sy), int(region[2] * sx), int(region[3] * sy)]
        return read_image(session, {"observation_id": item["id"], "crop": crop})
    if name == "cursor_position":
        x, y = _cursor(item)
        return ToolResult(True, f"X={x}, Y={y}", 0)
    if name == "wait":
        duration = args.get("duration", 1)
        if type(duration) not in (int, float) or not 0 <= duration <= 30 or not math.isfinite(duration):
            raise ValueError("wait duration must be 0-30 seconds here")
        if cancel.wait(duration):
            raise ValueError("Computer action cancelled")
        return native(session, "screenshot", {}, cancel)
    return act(session, {"observation_id": item["id"], "actions": _native_actions(name, args, item)}, cancel)


def tools(session: str, cancel: threading.Event):
    from agent.lean.toolbox import NativeTool
    capture_schema = {"type": "object", "properties": {
        "monitor": {"type": "integer", "description": "Monitor number, starting at 1."},
        "window_title": {"type": "string", "description": "Capture one visible window by its unique title."}}}
    action_schema = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["click", "double_click", "triple_click", "right_click", "move", "move_relative", "drag", "scroll", "type", "key", "hold", "wait"]},
        "coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2,
                       "description": "[x,y] in the attached screenshot's model coordinate space. Optional for hold; omit to keep the current pointer."},
        "start_coordinate": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
        "delta": {"type": "array", "items": {"type": "integer", "minimum": -1000, "maximum": 1000}, "minItems": 2, "maxItems": 2,
                  "description": "move_relative only: [dx,dy] Windows relative motion, useful for pointer-locked game cameras. Sensitivity/acceleration apply."},
        "button": {"type": "string", "enum": ["left", "right", "middle"],
                   "description": "Mouse button for click/drag/hold. hold can combine a button with text keys, e.g. left plus w."},
        "text": {"type": "string", "description": "Text for type, or up to five keys joined with + for key/hold, e.g. shift+w."},
        "duration": {"type": "number", "minimum": 0, "maximum": MAX_ACTION_SECONDS},
        "amount": {"type": "integer", "minimum": -20, "maximum": 20},
        "horizontal": {"type": "boolean", "description": "scroll only: true scrolls sideways (positive amount = right)."}}, "required": ["action"]}
    return [NativeTool("take_screenshot", "Capture a monitor or visible window. Returns the actual image to you and chat, plus its observation ID and coordinate size.", capture_schema, lambda a: screenshot(session, a)),
            NativeTool("vision_qa", "Capture the screen so YOU can answer the question visually using your current model. The result includes the image, not OCR.",
                       {**capture_schema, "properties": {**capture_schema["properties"], "question": {"type": "string"}}}, lambda a: screenshot(session, a)),
            NativeTool("read_image", "View an existing screenshot by observation_id or an allowed project image by path. Optional crop [left,top,right,bottom] uses ORIGINAL pixels; crops cannot control the screen.",
                       {"type": "object", "properties": {"observation_id": {"type": "string"}, "path": {"type": "string"}, "crop": {"type": "array", "items": {"type": "integer"}}}}, lambda a: read_image(session, a)),
            NativeTool("computer_action", "Control the desktop using a FRESH screenshot ID. Coordinates use its model image size. Run 1–12 bounded actions, at most ten seconds of total requested durations, then inspect the returned screenshot. hold accepts text keys, a mouse button, or both for up to five seconds; inputs release before the next action. move_relative uses delta for pointer-locked cameras. Requires desktop permission and owner approval.",
                       {"type": "object", "properties": {"observation_id": {"type": "string"}, "actions": {"type": "array", "items": action_schema, "minItems": 1, "maxItems": 12}}, "required": ["observation_id", "actions"]}, lambda a: act(session, a, cancel))] + [
            # Claude's own action names (computer_toolset_20260801). Hidden: never sent as schemas;
            # the Anthropic adapter declares the toolset instead, and calls arrive under these names.
            NativeTool(member, f"Claude computer toolset: {member}", {"type": "object", "properties": {}},
                       lambda a, member=member: native(session, member, a, cancel), hidden=True, rides_with="computer_action")
            for member in sorted(NATIVE_MEMBERS)]
