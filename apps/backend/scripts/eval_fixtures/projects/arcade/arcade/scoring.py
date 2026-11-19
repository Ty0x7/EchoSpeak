"""Points, combos and the high-score table."""
from __future__ import annotations

import json
from pathlib import Path

# Points for destroying one enemy of each kind.
POINTS = {"drone": 100, "tank": 300, "boss": 1000}
COMBO_STEP = 5  # every five kills in a row without being hit add one to the multiplier


class Scoreboard:
    def __init__(self) -> None:
        self.score = 0
        self.kills: dict[str, int] = {}
        self.combo = 0
        self.best_combo = 0

    def multiplier(self) -> int:
        return 1 + self.combo // COMBO_STEP

    def record_kill(self, kind: str) -> int:
        """Add the points for one destroyed enemy of this kind; returns the points added."""
        points = POINTS.get(kind, 50) * self.multiplier()
        self.score += points
        self.kills[kind] = self.kills.get(kind, 0) + 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        return points

    def player_hit(self) -> None:
        self.combo = 0

    def summary(self) -> str:
        kills = ", ".join(f"{count} {kind}" for kind, count in sorted(self.kills.items())) or "no kills"
        return f"{self.score} points ({kills}; best combo {self.best_combo})"


class HighScores:
    """The ten best scores, kept in a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.entries: list[dict] = []
        if self.path.exists():
            self.entries = json.loads(self.path.read_text())

    def qualifies(self, score: int) -> bool:
        return len(self.entries) < 10 or score > min(e["score"] for e in self.entries)

    def add(self, name: str, score: int) -> int:
        """Insert a score; returns its rank (1 = best), or 0 if it didn't make the table."""
        if not self.qualifies(score):
            return 0
        self.entries.append({"name": name, "score": score})
        self.entries.sort(key=lambda e: -e["score"])
        self.entries = self.entries[:10]
        self.path.write_text(json.dumps(self.entries, indent=2))
        return next(i for i, e in enumerate(self.entries, 1) if e["name"] == name and e["score"] == score)
