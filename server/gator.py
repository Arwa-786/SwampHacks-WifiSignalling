"""The AI gator host, on Vultr Serverless Inference.

    python gator.py --list                 # models and voices this key can use
    python gator.py --try                  # one taunt for a made-up round

Three jobs:
  taunt(round)    one spoken line after a catch or a win ("You froze for 41 s...")
  rescue(room)    one sentence for the Fire rescue / Guardian screen
  voice cache     fixed cues ("Red light!") rendered once with Vultr text-to-speech,
                  then played from disk, so the booth never waits on the network

Rules that keep the game safe from a slow or offline network:
  * Nothing here is ever on the red-light path. The Wi-Fi verdict and the game
    don't wait for any of it.
  * Every AI call has a deadline. Miss it, or produce a line that fails the
    checks below, and a fixed fallback line is used instead.
  * The model only gets game facts (times, counts, motion ratios), never names.

Two ways to give the model the facts, compared by gator_eval.py:
  "tools"   the model calls our functions (get_round, get_catch_signal, ...) and
            picks what to mention. Not every Vultr model supports it: gator_eval.py checks.
  "prompt"  we put all the facts in the prompt: one round trip, works on any model.

Key: VULTR_INFERENCE_API_KEY in server/.env (Vultr console > Serverless Inference).
API: https://docs.vultr.com/how-to-use-tool-calling-with-vultr-serverless-inference
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time

import httpx

from store import load_env

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://api.vultrinference.com/v1"
# The docs name kimi-k2-instruct as the only tool-calling model, but the console's
# Oct 2026 list doesn't have it: pick from `--list` with gator_eval.py, then set
# VULTR_CHAT_MODEL in server/.env.
VOICE_DIR = os.path.join(HERE, "voice_cache")

MAX_WORDS = 22  # a spoken line has to fit in the pause before the next round
# The model narrating its own plan instead of talking to the player (seen in the
# eval: "Let me look up the round facts.", "I need the catch signal first.", "I won!")
META = re.compile(r"\b(let me|i need|i should|i will|i'll|look(ing)? up|the facts|tool|catch signal|i won)\b", re.I)
BANNED = re.compile(r"\b(stupid|idiot|loser|dumb|fat|ugly|kill|die|dead)\b", re.I)  # young audience

FALLBACK = {
    "OUT": ["The Wi-Fi felt that one. You're out!", "Gotcha. The signal never lies.",
            "So close. One twitch and the Wi-Fi saw it."],
    "WON": ["You made it! Not even the Wi-Fi could catch you.", "Frozen like a statue. You win!"],
}

SYSTEM = (
    "You are Gator, the host of a Red Light, Green Light booth at a student hackathon. "
    "A hidden player behind a curtain is judged by Wi-Fi signals alone. "
    f"Reply with ONE spoken line, at most {MAX_WORDS} words, playful and kind, for all ages. "
    "Use at most one number, and only numbers from the facts. Never invent facts. "
    "booth_today numbers are totals for everyone today, not this player. "
    "No emojis, no hashtags, no quotes, no stage directions."
)
RESCUE_SYSTEM = (
    "You write status lines for a firefighter's screen. Wi-Fi sensing reports what it sees in a room. "
    "Reply with ONE plain sentence under 25 words: is someone there, are they moving or still, for how long, "
    "and a priority (Priority 1 = person present and not moving). Only use the facts given; if unsure, say so."
)


# ---------------- the tools the model can call ----------------

TOOLS = [
    {"type": "function", "function": {
        "name": "get_round",
        "description": "How the round went: result (OUT or WON), seconds played, red lights survived.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_catch_signal",
        "description": "What the Wi-Fi saw at the moment of the catch: peak motion as a multiple of the "
                       "still threshold, how many of the 3 receivers saw it, and how many seconds after "
                       "red light was called the player got caught.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_scoreboard",
        "description": "Totals for EVERYONE who played at the booth today (not this player): "
                       "players caught, players who won, red lights called.",
        "parameters": {"type": "object", "properties": {}}}},
]


def run_tool(name: str, facts: dict) -> dict:
    """Answer a tool call from the facts we already gathered. The tools only
    read; the model can't change the game."""
    if name == "get_round":
        return {k: facts.get(k) for k in ("result", "seconds_played", "red_lights_survived")}
    if name == "get_catch_signal":
        return facts.get("catch") or {"note": "the player was not caught"}
    if name == "get_scoreboard":
        return facts.get("booth_today") or {"note": "no totals yet"}
    return {"error": f"unknown tool {name}"}


# ---------------- checks on every line ----------------

def booth_today(scoreboard: dict) -> dict:
    """Store.scoreboard() renamed so a model can't mistake it for this player's stats
    (it did: "you almost made it through all 140 red lights")."""
    return {"players_caught_today": scoreboard.get("caught"), "players_won_today": scoreboard.get("won"),
            "red_lights_called_today": scoreboard.get("red_lights")}


UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def spoken_numbers(text: str) -> list[str]:
    """Numbers in a line, digits or words ("fifty-two" -> "52"), so a made-up
    number can't slip past the check by being spelled out."""
    found = re.findall(r"\d+(?:\.\d+)?", text)
    words = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    i = 0
    while i < len(words):
        w = words[i]
        if w in TENS:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            unit = UNITS.index(nxt) if nxt in UNITS[1:10] else 0
            found.append(str(TENS[w] + unit))
            i += 2 if unit else 1
            continue
        if w in UNITS[2:]:  # "zero"/"one" read as words too often ("one twitch")
            found.append(str(UNITS.index(w)))
        i += 1
    return found


def numbers_in(value) -> set[str]:
    """Every number in the facts, as written and rounded, so '41.3 s' allows '41'."""
    found = set()
    for n in re.findall(r"\d+(?:\.\d+)?", json.dumps(value)):
        found.add(n)
        found.add(str(round(float(n))))
    return found


def check_line(text: str, facts: dict) -> list[str]:
    """Reasons a line can't be spoken (empty list = fine)."""
    problems = []
    words = text.split()
    if not words:
        problems.append("empty")
    if len(words) > MAX_WORDS:
        problems.append(f"too long ({len(words)} words)")
    if BANNED.search(text):
        problems.append("not kind")
    if META.search(text):
        problems.append("not a line for the player")
    invented = [n for n in spoken_numbers(text) if n not in numbers_in(facts)]
    if invented:
        problems.append(f"invented numbers {invented}")
    return problems


def clean(text: str) -> str:
    text = re.sub(r"[*_#\"“”]", "", text or "").strip()
    return text.split("\n")[0].strip()


# ---------------- Vultr client ----------------

class Vultr:
    def __init__(self, key: str | None = None, http: httpx.Client | None = None):
        load_env()
        self.key = key or os.environ.get("VULTR_INFERENCE_API_KEY")
        self.http = http or httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {self.key}"})

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def chat(self, messages: list[dict], model: str, tools: list | None = None, max_tokens: int = 80,
             temperature: float = 0.9, timeout: float = 4.0, reasoning: str | None = "none") -> dict:
        """reasoning: Vultr's reasoning_effort. "none" skips the model's hidden
        thinking, which costs time and tokens a one-line quip doesn't need."""
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if reasoning:
            body["reasoning_effort"] = reasoning
        if tools:
            body |= {"tools": tools, "tool_choice": "auto"}
        r = self.http.post("/chat/completions", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    def speech(self, text: str, model: str, voice: str, instructions: str | None = None,
               timeout: float = 30.0) -> bytes:
        body = {"model": model, "input": text, "voice": voice}
        if instructions:  # how to say it (qwen3-tts), e.g. "an excited game-show host"
            body["instructions"] = instructions
        r = self.http.post("/audio/speech", json=body, timeout=timeout)
        r.raise_for_status()
        return r.content  # MP3 (24 kHz mono), though the docs say WAV

    def list(self, what: str) -> list:
        path = {"models": "/models", "audio_models": "/audio/models"}.get(what) or f"/audio/voices?model={what[7:]}"
        r = self.http.get(path)
        r.raise_for_status()
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data


# ---------------- the gator ----------------

class Gator:
    def __init__(self, client: Vultr | None = None, model: str | None = None, mode: str = "tools",
                 deadline_s: float = 3.0, reasoning: str | None = "none"):
        self.client = client or Vultr()  # also loads server/.env
        self.model = model or os.environ.get("VULTR_CHAT_MODEL", "")
        self.mode, self.deadline_s, self.reasoning = mode, deadline_s, reasoning
        self.last: dict = {}  # how the last line was made, for /api/ai/last and the eval

    def taunt(self, facts: dict) -> str:
        """A checked line for this round, or a fallback. Never raises."""
        start = time.monotonic()
        text, why, calls = "", "", []
        if self.client.enabled:
            try:
                text, calls = (self._with_tools if self.mode == "tools" else self._with_prompt)(facts, start)
                problems = check_line(text, facts)
                why = "; ".join(problems)
            except Exception as e:  # network, timeout, bad JSON: the booth keeps going
                why = f"{type(e).__name__}: {e}"[:200]
        else:
            why = "no VULTR_INFERENCE_API_KEY"
        used_ai = bool(text) and not why
        line = text if used_ai else random.choice(FALLBACK.get(facts.get("result"), FALLBACK["OUT"]))
        self.last = {"line": line, "ai": used_ai, "rejected": text if text and why else None, "why": why or None,
                     "tool_calls": calls, "seconds": round(time.monotonic() - start, 2),
                     "model": self.model, "mode": self.mode, "reasoning": self.reasoning}
        return line

    def _left(self, start: float) -> float:
        left = self.deadline_s - (time.monotonic() - start)
        if left <= 0.2:
            raise TimeoutError("deadline")
        return left

    def _with_prompt(self, facts: dict, start: float) -> tuple[str, list]:
        msg = [{"role": "system", "content": SYSTEM},
               {"role": "user", "content": "Facts: " + json.dumps(facts) + "\nYour line:"}]
        reply = self.client.chat(msg, self.model, timeout=self._left(start), reasoning=self.reasoning)
        return clean(reply.get("content")), []

    def _with_tools(self, facts: dict, start: float) -> tuple[str, list]:
        """Let the model look things up, at most 2 rounds of tool calls (each
        round trip costs ~0.5-1 s, and the line has to arrive in time)."""
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"The round just ended: {facts.get('result')}. "
                                            "Look up what you need, then give your line."}]
        calls = []
        for _ in range(3):
            reply = self.client.chat(msgs, self.model, tools=TOOLS if len(calls) < 4 else None,
                                     timeout=self._left(start), reasoning=self.reasoning)
            if not reply.get("tool_calls"):
                return clean(reply.get("content")), calls
            msgs.append({"role": "assistant", "content": reply.get("content"), "tool_calls": reply["tool_calls"]})
            for call in reply["tool_calls"]:
                name = call["function"]["name"]
                calls.append(name)
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(run_tool(name, facts))})
        raise RuntimeError("too many tool calls")

    def rescue(self, room: dict) -> str:
        """One sentence for the Fire rescue screen. Falls back to a template."""
        if self.client.enabled:
            try:
                msg = [{"role": "system", "content": RESCUE_SYSTEM},
                       {"role": "user", "content": "Facts: " + json.dumps(room)}]
                text = clean(self.client.chat(msg, self.model, max_tokens=60, temperature=0.2,
                                              timeout=self.deadline_s).get("content"))
                if text and not check_line(text, room) and len(text.split()) <= 25:
                    return text
            except Exception:
                pass
        return rescue_template(room)


def rescue_template(room: dict) -> str:
    state, secs = room.get("state"), round(room.get("seconds_in_state") or 0)
    if state == "EMPTY":
        return f"{room.get('room', 'Room')}: no one detected."
    if state == "MOVING":
        return f"{room.get('room', 'Room')}: person detected, moving. Priority 2."
    return f"{room.get('room', 'Room')}: person detected, not moving for {secs} s. Priority 1."


# ---------------- voice: fixed cues rendered once ----------------

class Voice:
    """Speaks through cached Vultr TTS audio when there is some, else macOS `say`.
    `prerender` makes the recordings ahead of time, so playback never waits on the network."""

    def __init__(self, client: Vultr | None = None, model: str | None = None, voice: str | None = None,
                 folder: str = VOICE_DIR):
        load_env()
        self.client = client or Vultr()
        self.model = model or os.environ.get("VULTR_TTS_MODEL")
        self.voice = voice or os.environ.get("VULTR_TTS_VOICE")
        self.instructions = os.environ.get("VULTR_TTS_INSTRUCTIONS")
        self.folder = folder

    def path(self, text: str) -> str:
        key = hashlib.sha1(f"{self.model}|{self.voice}|{self.instructions}|{text}".encode()).hexdigest()[:16]
        return os.path.join(self.folder, f"{key}.mp3")

    def prerender(self, lines: list[str]) -> int:
        if not (self.client.enabled and self.model and self.voice):
            return 0
        os.makedirs(self.folder, exist_ok=True)
        made = 0
        for text in lines:
            if not os.path.exists(self.path(text)):
                with open(self.path(text), "wb") as f:
                    f.write(self.client.speech(text, self.model, self.voice, self.instructions))
                made += 1
        return made

    def command(self, text: str) -> list[str]:
        """What to run to say `text`: the cached recording, or macOS `say`."""
        cached = self.path(text)
        return ["afplay", cached] if os.path.exists(cached) else ["say", "-r", "220", text]


# ---------------- command line ----------------

EXAMPLE = {"result": "OUT", "seconds_played": 41.3, "red_lights_survived": 3,
           "catch": {"peak_motion_x_threshold": 4.2, "receivers_that_saw_it": 3, "caught_seconds_after_red_light_called": 1.4},
           "booth_today": {"players_caught_today": 12, "players_won_today": 3, "red_lights_called_today": 57}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="models and voices this key can use")
    parser.add_argument("--try", dest="try_", action="store_true", help="one taunt for a made-up round")
    parser.add_argument("--model", help="default: VULTR_CHAT_MODEL in server/.env")
    parser.add_argument("--mode", choices=["tools", "prompt"], default="tools")
    args = parser.parse_args()
    client = Vultr()
    if not client.enabled:
        raise SystemExit("Put VULTR_INFERENCE_API_KEY=... in server/.env first.")
    if args.list:
        for m in client.list("models"):
            print(m["id"], [o["type"] for o in m.get("output_modalities", [])])
        for m in client.list("audio_models").get("speech", []):
            for v in client.list(f"voices:{m['id']}"):
                print(f"  voice {m['id']} {v['id']}: {v.get('native_language')}, {v.get('description')}")
    if args.try_:
        gator = Gator(client, model=args.model, mode=args.mode, deadline_s=8)
        print(gator.taunt(EXAMPLE))
        print(json.dumps(gator.last, indent=1))


if __name__ == "__main__":
    main()
