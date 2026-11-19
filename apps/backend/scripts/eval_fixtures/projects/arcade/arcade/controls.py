"""Keyboard to game actions: which keys are held, and which went down this frame."""
from __future__ import annotations

# Key names as the window reports them, and the action each one means.
KEYMAP = {
    "ArrowUp": "move_up",
    "ArrowDown": "move_down",
    "ArrowLeft": "move_left",
    "ArrowRight": "move_right",
    "KeyW": "move_up",
    "KeyS": "move_down",
    "KeyA": "move_left",
    "KeyD": "move_right",
    "Space": "fire",
    "KeyP": "pause",
    "Escape": "quit",
}


class Controls:
    def __init__(self, keymap: dict[str, str] | None = None) -> None:
        self.keymap = dict(keymap or KEYMAP)
        self.held: set[str] = set()
        self.pressed: set[str] = set()  # went down during this frame

    def key_down(self, key: str) -> None:
        action = self.keymap.get(key)
        if not action:
            return
        if action not in self.held:
            self.pressed.add(action)
        self.held.add(action)

    def key_up(self, key: str) -> None:
        action = self.keymap.get(key)
        if action:
            self.held.discard(action)

    def actions(self) -> set[str]:
        """Every action held down right now."""
        return set(self.held)

    def just_pressed(self, action: str) -> bool:
        return action in self.pressed

    def end_frame(self) -> None:
        self.pressed.clear()

    def rebind(self, key: str, action: str) -> None:
        """Point a key at another action; a key bound to nothing is removed."""
        if action:
            self.keymap[key] = action
        else:
            self.keymap.pop(key, None)

    def keys_for(self, action: str) -> list[str]:
        return sorted(key for key, bound in self.keymap.items() if bound == action)

    def describe(self) -> str:
        """One line per action, for the help screen."""
        actions = sorted(set(self.keymap.values()))
        return "\n".join(f"{action:>10}: {', '.join(self.keys_for(action))}" for action in actions)
