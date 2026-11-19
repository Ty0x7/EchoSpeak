"""The play field: everything in it, moved one step at a time."""
from __future__ import annotations

from arcade.entities import Bullet, Enemy, Player, Vec, enemy_stats
from arcade.scoring import Scoreboard

BULLET_SPEED = 400.0


class World:
    def __init__(self, width: int = 640, height: int = 480, scoreboard: Scoreboard | None = None) -> None:
        self.width, self.height = width, height
        self.player = Player(Vec(width / 2, height - 40))
        self.enemies: list[Enemy] = []
        self.bullets: list[Bullet] = []
        self.scoreboard = scoreboard or Scoreboard()
        self.time = 0.0

    # ── things entering the world ───────────────────────────────────
    def spawn_enemy(self, kind: str, x: float, y: float) -> Enemy:
        stats = enemy_stats(kind)
        enemy = Enemy(Vec(x, y), radius=stats["radius"], kind=kind, speed=stats["speed"], health=int(stats["health"]))
        self.enemies.append(enemy)
        return enemy

    def fire(self) -> Bullet:
        start = self.player.pos + self.player.facing.scaled(self.player.radius + 4)
        bullet = Bullet(start, velocity=self.player.facing.scaled(BULLET_SPEED))
        self.bullets.append(bullet)
        return bullet

    # ── one step ─────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        """Move everything by dt seconds, then resolve collisions and remove what died."""
        self.time += dt
        self.player.update(dt)
        self._keep_inside(self.player)
        for enemy in self.enemies:
            enemy.update(dt, self.player.pos)
        for bullet in self.bullets:
            bullet.update(dt)
            if not (0 <= bullet.pos.x <= self.width and 0 <= bullet.pos.y <= self.height):
                bullet.alive = False
        self._collisions()
        self.enemies = [e for e in self.enemies if e.alive]
        self.bullets = [b for b in self.bullets if b.alive]

    def _keep_inside(self, player: Player) -> None:
        player.pos = Vec(min(max(player.pos.x, player.radius), self.width - player.radius),
                         min(max(player.pos.y, player.radius), self.height - player.radius))

    def _collisions(self) -> None:
        for bullet in self.bullets:
            for enemy in self.enemies:
                if bullet.touches(enemy):
                    bullet.alive = False
                    if enemy.damage():
                        self.scoreboard.record_kill(enemy)
                    break
        for enemy in self.enemies:
            if enemy.touches(self.player):
                enemy.alive = False
                self.scoreboard.player_hit()
                self.player.hit()

    # ── looking at it ───────────────────────────────────────────────
    def snapshot(self) -> dict:
        """Plain data for a renderer or a save file."""
        return {
            "time": round(self.time, 3),
            "player": {"x": self.player.pos.x, "y": self.player.pos.y, "lives": self.player.lives},
            "enemies": [{"kind": e.kind, "x": e.pos.x, "y": e.pos.y, "health": e.health} for e in self.enemies],
            "bullets": len(self.bullets),
            "score": self.scoreboard.score,
        }

    def render_text(self, columns: int = 32, rows: int = 12) -> str:
        """A rough character map of the field: P player, E enemy, . bullet."""
        grid = [[" "] * columns for _ in range(rows)]

        def put(pos: Vec, char: str) -> None:
            col = min(columns - 1, max(0, int(pos.x / self.width * columns)))
            row = min(rows - 1, max(0, int(pos.y / self.height * rows)))
            grid[row][col] = char

        for bullet in self.bullets:
            put(bullet.pos, ".")
        for enemy in self.enemies:
            put(enemy.pos, "E")
        put(self.player.pos, "P")
        return "\n".join("".join(row) for row in grid)
