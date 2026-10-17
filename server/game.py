"""Red Light, Green Light rules, independent of sensing and networking.

The sensing side calls `tick(now, sensed)` ~10x per second with the detector's
state (EMPTY / STILL / MOVING). Everyone else (local screen, Roblox, database,
AI host) reads `snapshot()` and `events`.

Rules (CLAUDE.md, Play mode):
  * GREEN: moving fills the progress bar; reach 100% to win.
  * RED: any MOVING after a short grace period eliminates the player.
  * Anyone (the host, or a Roblox player) can call red light early.
The grace period covers the player's reaction time plus the detector's ~0.5 s
delay, so a player who freezes promptly is never eliminated by the lag.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

LOBBY, GREEN, RED, OUT, WON = "LOBBY", "GREEN", "RED", "OUT", "WON"
MOVING = "MOVING"

GRACE_S = 1.0  # after "red light": reaction time + detector delay
PROGRESS_PER_S = 8.0  # % per second of movement during green (~12 s of moving to win)
GREEN_RANGE_S = (2.5, 6.0)
RED_RANGE_S = (3.0, 6.0)


@dataclass
class Event:
    t: float
    kind: str  # "start", "green", "red", "eliminated", "won", "red_called"
    detail: str = ""


@dataclass
class Round:
    player: str
    started: float
    progress: float = 0.0
    red_seconds: float = 0.0  # time spent under red light, after grace
    red_still_seconds: float = 0.0
    ended: float | None = None


@dataclass
class Game:
    rng: random.Random = field(default_factory=random.Random)
    phase: str = LOBBY
    phase_started: float = 0.0
    phase_ends: float = 0.0
    round: Round | None = None
    sensed: str = "EMPTY"
    events: list[Event] = field(default_factory=list)
    _last_tick: float | None = None

    def start(self, now: float, player: str = "Player") -> None:
        self.round = Round(player=player, started=now)
        self.events.append(Event(now, "start", player))
        self._last_tick = now
        self._enter(GREEN, now)

    def call_red(self, now: float, by: str = "host") -> bool:
        """Red light right now (e.g. a Roblox player pressed the button).
        Ignored unless it's currently green."""
        if self.phase != GREEN:
            return False
        self.events.append(Event(now, "red_called", by))
        self._enter(RED, now)
        return True

    def tick(self, now: float, sensed: str) -> None:
        self.sensed = sensed
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.phase not in (GREEN, RED) or self.round is None:
            return
        r = self.round
        if self.phase == GREEN:
            if sensed == MOVING:
                r.progress = min(100.0, r.progress + PROGRESS_PER_S * dt)
            if r.progress >= 100.0:
                self._finish(now, WON, "won", f"{now - r.started:.1f}s")
                return
        elif now - self.phase_started >= GRACE_S:
            r.red_seconds += dt
            if sensed == MOVING:
                self._finish(now, OUT, "eliminated", f"moved {now - self.phase_started:.1f}s into red")
                return
            r.red_still_seconds += dt
        if now >= self.phase_ends:
            self._enter(RED if self.phase == GREEN else GREEN, now)

    def snapshot(self, now: float) -> dict:
        """Everything a screen or Roblox needs; no personal data beyond the nickname."""
        r = self.round
        return {
            "phase": self.phase,
            "sensed": self.sensed,
            "phase_left": max(0.0, self.phase_ends - now) if self.phase in (GREEN, RED) else 0.0,
            "in_grace": self.phase == RED and now - self.phase_started < GRACE_S,
            "round": None
            if r is None
            else {
                **asdict(r),
                "survival_s": (r.ended or now) - r.started,
                "stillness": r.red_still_seconds / r.red_seconds if r.red_seconds else 1.0,
            },
        }

    def _enter(self, phase: str, now: float) -> None:
        self.phase = phase
        self.phase_started = now
        lo, hi = GREEN_RANGE_S if phase == GREEN else RED_RANGE_S
        self.phase_ends = now + self.rng.uniform(lo, hi)
        self.events.append(Event(now, phase.lower()))

    def _finish(self, now: float, phase: str, kind: str, detail: str) -> None:
        self.phase = phase
        self.round.ended = now
        self.events.append(Event(now, kind, detail))
