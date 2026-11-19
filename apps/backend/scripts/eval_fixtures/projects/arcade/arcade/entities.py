"""Things that live in the world: the player, enemies and bullets."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Vec:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: "Vec") -> "Vec":
        return Vec(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec") -> "Vec":
        return Vec(self.x - other.x, self.y - other.y)

    def scaled(self, k: float) -> "Vec":
        return Vec(self.x * k, self.y * k)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> "Vec":
        size = self.length()
        return Vec(self.x / size, self.y / size) if size else Vec()

    def distance_to(self, other: "Vec") -> float:
        return (self - other).length()


@dataclass
class Entity:
    pos: Vec
    radius: float = 8.0
    alive: bool = True

    def touches(self, other: "Entity") -> bool:
        """True when both are alive and their circles overlap."""
        return self.alive and other.alive and self.pos.distance_to(other.pos) <= self.radius + other.radius


@dataclass
class Player(Entity):
    speed: float = 120.0
    lives: int = 3
    facing: Vec = field(default_factory=lambda: Vec(0, -1))
    velocity: Vec = field(default_factory=Vec)
    invulnerable: float = 0.0

    # The actions that move the player, and the direction of each.
    MOVES = {"up": Vec(0, -1), "down": Vec(0, 1), "left": Vec(-1, 0), "right": Vec(1, 0)}

    def apply(self, actions: set[str]) -> None:
        """Set this frame's velocity from the movement actions held down ("up", "left", ...)."""
        direction = Vec()
        for action in actions:
            if action in self.MOVES:
                direction = direction + self.MOVES[action]
        if direction.length():
            self.facing = direction.normalized()
            self.velocity = self.facing.scaled(self.speed)
        else:
            self.velocity = Vec()

    def update(self, dt: float) -> None:
        self.pos = self.pos + self.velocity.scaled(dt)
        self.invulnerable = max(0.0, self.invulnerable - dt)

    def hit(self) -> bool:
        """Lose a life (unless just hit); True when the player has none left."""
        if self.invulnerable > 0:
            return False
        self.lives -= 1
        self.invulnerable = 1.0
        return self.lives <= 0


@dataclass
class Enemy(Entity):
    kind: str = "drone"
    speed: float = 40.0
    health: int = 1

    def update(self, dt: float, target: Vec) -> None:
        """Drift toward the target."""
        self.pos = self.pos + (target - self.pos).normalized().scaled(self.speed * dt)

    def damage(self, amount: int = 1) -> bool:
        """Take damage; True when this destroyed the enemy."""
        self.health -= amount
        if self.health <= 0:
            self.alive = False
            return True
        return False


@dataclass
class Bullet(Entity):
    velocity: Vec = field(default_factory=Vec)
    ttl: float = 1.5
    radius: float = 3.0

    def update(self, dt: float) -> None:
        self.pos = self.pos + self.velocity.scaled(dt)
        self.ttl -= dt
        if self.ttl <= 0:
            self.alive = False


def enemy_stats(kind: str) -> dict[str, float]:
    """Speed, health and size for each kind of enemy."""
    table = {
        "drone": {"speed": 40.0, "health": 1, "radius": 8.0},
        "tank": {"speed": 20.0, "health": 3, "radius": 14.0},
        "boss": {"speed": 12.0, "health": 10, "radius": 24.0},
    }
    return dict(table.get(kind, table["drone"]))
