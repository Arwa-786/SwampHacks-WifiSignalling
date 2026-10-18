import json

from gator import FALLBACK, Gator, Voice, check_line, rescue_template
from app import round_facts

FACTS = {"result": "OUT", "seconds_played": 41.3, "red_lights_survived": 3,
         "catch": {"peak_motion_x_threshold": 4.2, "receivers_that_saw_it": 3, "caught_seconds_after_red_light_called": 1.4}}


class FakeVultr:
    """Plays back scripted replies; records what was asked."""

    enabled = True

    def __init__(self, replies):
        self.replies, self.asked = list(replies), []

    def chat(self, messages, model, tools=None, **kw):
        self.asked.append({"messages": [dict(m) for m in messages], "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool_call(name, i=0):
    return {"content": None, "tool_calls": [{"id": f"functions.{name}:{i}", "type": "function",
                                             "function": {"name": name, "arguments": "{}"}}]}


def test_checks_reject_long_unkind_or_made_up_lines():
    assert check_line("You lasted 41 seconds, then 3 boards saw you!", FACTS) == []
    assert "not kind" in check_line("Wow, you loser.", FACTS)
    assert any("invented" in p for p in check_line("You lasted 99 seconds.", FACTS))
    assert any("too long" in p for p in check_line("word " * 30, FACTS))


def test_tool_loop_answers_lookups_from_the_facts():
    client = FakeVultr([tool_call("get_catch_signal"), {"content": "4.2 times the still level. Gotcha!"}])
    gator = Gator(client, mode="tools")
    assert gator.taunt(FACTS) == "4.2 times the still level. Gotcha!"
    assert gator.last["ai"] and gator.last["tool_calls"] == ["get_catch_signal"]
    tool_msg = client.asked[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "functions.get_catch_signal:0"
    assert json.loads(tool_msg["content"])["receivers_that_saw_it"] == 3


def test_bad_line_or_network_error_falls_back_and_says_why():
    gator = Gator(FakeVultr([{"content": "You lasted 500 seconds!"}]), mode="prompt")
    assert gator.taunt(FACTS) in FALLBACK["OUT"]
    assert gator.last["rejected"] == "You lasted 500 seconds!" and "invented" in gator.last["why"]

    gator = Gator(FakeVultr([TimeoutError("slow")]), mode="prompt")
    assert gator.taunt(FACTS) in FALLBACK["OUT"] and "TimeoutError" in gator.last["why"]


def test_no_key_means_fixed_lines():
    class Off:
        enabled = False

    gator = Gator(Off())
    assert gator.taunt({"result": "WON"}) in FALLBACK["WON"] and not gator.last["ai"]


def test_round_facts_measure_the_catch_since_the_last_red():
    rnd = {"start": 100.0, "reds": 2, "red_t": 140.0}
    recent = [(139.0, [9.0, 9.0, 9.0]), (140.5, [0.5, 2.0, 0.8]), (141.0, [0.4, 3.5, 1.2])]
    facts = round_facts(rnd, "OUT", 141.4, recent)
    assert facts["seconds_played"] == 41.4 and facts["red_lights_survived"] == 1
    # the green-light movement before red (9.0) doesn't count
    assert facts["catch"] == {"peak_motion_x_threshold": 3.5, "receivers_that_saw_it": 2, "caught_seconds_after_red_light_called": 1.4}


def test_voice_plays_cached_audio_else_says_it(tmp_path):
    class TTS:
        enabled = True

        def speech(self, text, model, voice, instructions=None):
            return b"RIFF"

    voice = Voice(TTS(), model="m", voice="v", folder=str(tmp_path))
    assert voice.command("Red light!")[0] == "say"
    assert voice.prerender(["Red light!"]) == 1 and voice.prerender(["Red light!"]) == 0  # cached
    assert voice.command("Red light!") == ["afplay", voice.path("Red light!")]


def test_rescue_template_puts_still_people_first():
    assert rescue_template({"room": "Room 2", "state": "STILL", "seconds_in_state": 42}).endswith("Priority 1.")
    assert "no one" in rescue_template({"room": "Room 2", "state": "EMPTY"})


def test_spelled_out_numbers_are_checked_too():
    from gator import spoken_numbers

    assert spoken_numbers("fifty-two seconds, twelve times, 3 boards") == ["3", "52", "12"]
    assert check_line("You lasted forty-one seconds!", FACTS) == []
    assert any("invented" in p for p in check_line("You beat fifty-two red lights!", FACTS))


def test_model_narrating_its_plan_is_not_spoken():
    assert "not a line for the player" in check_line("Let me look up the round facts.", FACTS)
    assert "not a line for the player" in check_line("I won!", {"result": "WON"})
