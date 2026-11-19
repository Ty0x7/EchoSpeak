from arcade.game import Game


def test_the_game_runs_a_frame():
    game = Game()
    game.tick()
    assert game.frame == 1 and not game.over


def test_the_arrow_keys_move_the_player():
    game = Game()
    start = game.world.player.pos.y
    game.handle_key("ArrowUp")
    game.run(30)
    assert game.world.player.pos.y < start - 20


def test_shooting_a_drone_scores_its_points():
    game = Game()
    player = game.world.player.pos
    game.world.spawn_enemy("drone", player.x, player.y - 80)
    game.handle_key("Space")
    game.tick()
    game.handle_key("Space", down=False)
    game.run(40)
    assert game.world.scoreboard.score == 100
    assert not game.world.enemies
