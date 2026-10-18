"""Test harness for the AI gator: which model and mode should the booth use?

    python gator_eval.py --models MODEL_ID OTHER_ID        # each model, tools vs prompt
    python gator_eval.py --repeats 5 --deadline 3 --save   # --save: record in Tiger model_runs

Every variant gets the same made-up rounds (below), several times each, with
the same deadline the booth uses. Scored per variant:
  usable     the line passed every check in gator.check_line and arrived in time
             (otherwise the booth would have spoken a fallback line)
  invented   lines with a number that isn't in the facts (made-up stats)
  p50 / p95  seconds until the line was ready
  faithful   (with --judge) usable lines a second, larger model agrees say
             nothing false about the facts. The word checks can't see
             "Caught after 2.8 seconds" when it was 2.8 s after red light; a
             reader can. Eval only: the booth never waits on a judge.
  tool calls average lookups per line (tools mode; 0 with an unusable rate
             means the model ignored or rejected the tools)
Model IDs come from `python gator.py --list`.
Pick the variant with the most usable lines whose p95 fits the pause after a
catch (~3 s), then set it with app.py --gator-model / --gator-mode.
Sample lines go to data/gator_eval.json so a person can read them too:
the checks catch rule-breaking, not lines that just aren't funny.
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

from gator import Gator, Vultr

HERE = os.path.dirname(os.path.abspath(__file__))

ROUNDS = [
    {"result": "OUT", "seconds_played": 41.3, "red_lights_survived": 3,
     "catch": {"peak_motion_x_threshold": 4.2, "receivers_that_saw_it": 3, "caught_seconds_after_red_light_called": 1.4},
     "booth_today": {"players_caught_today": 12, "players_won_today": 3, "red_lights_called_today": 57}},
    {"result": "OUT", "seconds_played": 6.0, "red_lights_survived": 0,
     "catch": {"peak_motion_x_threshold": 1.3, "receivers_that_saw_it": 1, "caught_seconds_after_red_light_called": 2.8},
     "booth_today": {"players_caught_today": 4, "players_won_today": 0, "red_lights_called_today": 9}},
    {"result": "OUT", "seconds_played": 88.7, "red_lights_survived": 9,
     "catch": {"peak_motion_x_threshold": 12.5, "receivers_that_saw_it": 2, "caught_seconds_after_red_light_called": 1.1},
     "booth_today": {"players_caught_today": 30, "players_won_today": 8, "red_lights_called_today": 140}},
    {"result": "WON", "seconds_played": 52.0, "red_lights_survived": 5, "catch": None,
     "booth_today": {"players_caught_today": 12, "players_won_today": 4, "red_lights_called_today": 62}},
]


JUDGE = (
    "You check lines spoken by a game host against the facts of a Red Light, Green Light round. "
    "A line is faithful if everything it states about the round is true per the facts "
    "(jokes and encouragement are fine) AND it is something a host would say to the player. "
    "Unfaithful examples: wrong numbers, booth_today totals presented as this player's, "
    "'caught after 2.8 seconds' when 2.8 is seconds after red light, the host narrating its own plan "
    "('let me check...'), or speaking as the player ('I won!'). "
    'Reply with JSON only: {"faithful": true or false, "reason": "short"}'
)


def judge_line(client: Vultr, model: str, facts: dict, line: str) -> tuple[bool, str]:
    msgs = [{"role": "system", "content": JUDGE},
            {"role": "user", "content": f"Facts: {json.dumps(facts)}\nLine: {line}"}]
    # the judge may think (no deadline here), so give it room
    text = ""
    for reasoning in (None, "none"):  # thinking sometimes leaves no answer: retry without
        reply = client.chat(msgs, model, max_tokens=2000, temperature=0.0, timeout=60, reasoning=reasoning)
        text = reply.get("content") or ""
        try:
            verdict = json.loads(text[text.index("{"): text.rindex("}") + 1])
            return bool(verdict.get("faithful")), str(verdict.get("reason", ""))
        except ValueError:
            continue
    return False, f"judge reply unreadable: {text[:80]}"


def evaluate(gator: Gator, repeats: int, judge: str | None = None) -> tuple[dict, list[dict]]:
    samples = []
    for _ in range(repeats):
        for facts in ROUNDS:
            gator.taunt(facts)
            sample = dict(gator.last, result=facts["result"])
            if judge and sample["ai"]:
                sample["faithful"], sample["judge_reason"] = judge_line(gator.client, judge, facts, sample["line"])
            samples.append(sample)
    secs = np.array([s["seconds"] for s in samples])
    metrics = {
        "lines": len(samples),
        "usable": float(np.mean([s["ai"] for s in samples])),
        "invented": float(np.mean([bool(s["why"] and "invented" in s["why"]) for s in samples])),
        "p50_s": float(np.percentile(secs, 50)),
        "p95_s": float(np.percentile(secs, 95)),
        "tool_calls": float(np.mean([len(s["tool_calls"]) for s in samples])),
    }
    if judge:  # share of ALL lines that were both usable and faithful
        metrics["faithful"] = float(np.mean([bool(s.get("faithful")) for s in samples]))
    return metrics, samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True, help="model IDs (python gator.py --list)")
    parser.add_argument("--reasoning", nargs="+", default=["none", "default"],
                        help='reasoning_effort values to compare ("default" = the model\'s own)')
    parser.add_argument("--judge", help="model that reads each usable line against the facts, e.g. qwen3.8-27b")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--deadline", type=float, default=3.0, help="seconds; same as the booth")
    parser.add_argument("--save", action="store_true", help="also record each variant in Tiger model_runs")
    args = parser.parse_args()

    client = Vultr()
    if not client.enabled:
        raise SystemExit("Put VULTR_INFERENCE_API_KEY=... in server/.env first.")
    variants = [(m, mode, r) for m in args.models for mode in ("tools", "prompt") for r in args.reasoning]

    print("| model | mode | reasoning | usable | faithful | invented numbers | p50 s | p95 s | tool calls/line |")
    print("|---|---|---|---|---|---|---|---|---|")
    report = {}
    for model, mode, reasoning in variants:
        gator = Gator(client, model=model, mode=mode, deadline_s=args.deadline,
                      reasoning=None if reasoning == "default" else reasoning)
        metrics, samples = evaluate(gator, args.repeats, args.judge)
        name = f"{model}/{mode}/{reasoning}"
        report[name] = {"metrics": metrics, "samples": samples}
        print(f"| {model} | {mode} | {reasoning} | {metrics['usable']:.0%} | {metrics.get('faithful', float('nan')):.0%} | {metrics['invented']:.0%} | "
              f"{metrics['p50_s']:.2f} | {metrics['p95_s']:.2f} | {metrics['tool_calls']:.1f} |", flush=True)
        if args.save:
            from store import Store

            Store().add_model_run(f"gator:{name}", f"{len(ROUNDS)} rounds x {args.repeats}",
                                  metrics | {"deadline_s": args.deadline})

    out = os.path.join(HERE, "..", "sensing", "host", "data", "gator_eval.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    print(f"\nSample lines: {os.path.relpath(out)}")


if __name__ == "__main__":
    main()
