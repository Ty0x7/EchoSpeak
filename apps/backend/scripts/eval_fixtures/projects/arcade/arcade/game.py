"""The game loop: input in, one step of the world, state out."""
from __future__ import annotations

from arcade.controls import Controls
from arcade.world import World

FRAME = 1 / 60


class Game:
    def __init__(self, width: int = 640, height: int = 480) -> None:
        self.world = World(width, height)
        self.controls = Controls()
        self.paused = False
        self.over = False
        self.frame = 0

    def handle_key(self, key: str, down: bool = True) -> None:
        if down:
            self.controls.key_down(key)
        else:
            self.controls.key_up(key)

    def tick(self, dt: float = FRAME) -> None:
        """One frame: read the controls, move the world, check for the end."""
        if self.over:
            return
        if self.controls.just_pressed("quit"):
            self.over = True
        if self.controls.just_pressed("pause"):
            self.paused = not self.paused
        if not self.paused and not self.over:
            self.world.player.apply(self.controls.actions())
            if self.controls.just_pressed("fire"):
                self.world.fire()
            self.world.step(dt)
            if self.world.player.lives <= 0:
                self.over = True
        self.controls.end_frame()
        self.frame += 1

    def run(self, frames: int, dt: float = FRAME) -> None:
        for _ in range(frames):
            self.tick(dt)

    def status(self) -> str:
        state = "game over" if self.over else "paused" if self.paused else "playing"
        player = self.world.player
        return (f"frame {self.frame} · {state} · lives {player.lives} · "
                f"{self.world.scoreboard.summary()} · {len(self.world.enemies)} enemies")


def main() -> None:
    """A short scripted demo: a few drones, the player strafing and firing."""
    game = Game()
    for i in range(5):
        game.world.spawn_enemy("drone", 80 + i * 120, 40)
    game.handle_key("ArrowLeft")
    for frame in range(240):
        if frame % 20 == 0:
            game.handle_key("Space")
        elif frame % 20 == 1:
            game.handle_key("Space", down=False)
        game.tick()
    print(game.world.render_text())
    print(game.status())


if __name__ == "__main__":
    main()
