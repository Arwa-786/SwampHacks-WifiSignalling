"""One voice at a time for the booth.

Before this, every line started its own `say`/`afplay` process, so the scan
line, the hide line, the countdown and the gator could all talk over each other
(and with two Roblox servers running, every cue arrived twice).

Rules:
  - Lines play one after another, in order.
  - URGENT lines (the light calls the hider must hear on time: green, red, the
    countdown, caught, won) cut off whatever is playing and skip the queue.
  - The same line again within DUPLICATE_S is ignored (two game servers, retries).
  - A queued line that waited longer than STALE_S is dropped: old news.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from typing import Callable

DUPLICATE_S = 2.0
STALE_S = 10.0


class Speaker:
    def __init__(self, command: Callable[[str], list[str]], popen=subprocess.Popen, clock=time.monotonic):
        self._command = command
        self._popen = popen
        self._clock = clock
        self._queue: deque[tuple[float, str]] = deque()
        self._last: dict[str, float] = {}  # text -> when it was last accepted
        self._current = None
        self._cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text: str, urgent: bool = False) -> bool:
        """Queue `text`; returns False when it was dropped as a duplicate."""
        now = self._clock()
        with self._cond:
            if now - self._last.get(text, -1e9) < DUPLICATE_S:
                return False
            self._last[text] = now
            if urgent:
                self._queue.clear()          # older lines are out of date once the light changes
                self._stop_current()
            self._queue.append((now, text))
            self._cond.notify()
        return True

    def _stop_current(self) -> None:
        if self._current is not None and self._current.poll() is None:
            self._current.terminate()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue:
                    self._cond.wait()
                queued_at, text = self._queue.popleft()
                if self._clock() - queued_at > STALE_S:
                    continue
                proc = self._current = self._popen(self._command(text))
            proc.wait()  # outside the lock, so an urgent line can stop it
