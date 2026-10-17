import random

from game import GRACE_S, GREEN, OUT, RED, WON, Game


def run(game, start, seconds, sensed, step=0.1):
    t = start
    while t < start + seconds:
        game.tick(t, sensed)
        t += step
    return t


def new_game():
    g = Game(rng=random.Random(0))
    g.start(0.0, "Gator")
    return g


def test_moving_on_green_fills_progress_and_wins():
    g = new_game()
    g.phase_ends = 1e9  # stay green
    run(g, 0.0, 13.0, "MOVING")
    assert g.phase == WON
    assert g.events[-1].kind == "won"


def test_freezing_within_grace_is_safe_moving_after_is_out():
    g = new_game()
    g.call_red(1.0, by="roblox")
    g.phase_ends = 1e9  # stay red
    t = run(g, 1.0, GRACE_S - 0.2, "MOVING")  # still moving while reacting: allowed
    assert g.phase == RED
    t = run(g, t, 2.0, "STILL")
    assert g.phase == RED
    g.tick(t, "MOVING")
    assert g.phase == OUT
    assert [e.kind for e in g.events][-2:] == ["red", "eliminated"]
    assert g.snapshot(t)["round"]["stillness"] > 0.9


def test_red_call_only_during_green_and_phases_alternate():
    g = new_game()
    assert g.call_red(0.5)
    assert not g.call_red(0.6)  # already red
    end = g.phase_ends
    g.tick(end + 0.01, "STILL")
    assert g.phase == GREEN


def test_snapshot_has_only_game_state():
    snap = new_game().snapshot(0.5)
    assert snap["phase"] == GREEN
    assert set(snap) == {"phase", "sensed", "phase_left", "in_grace", "round"}
