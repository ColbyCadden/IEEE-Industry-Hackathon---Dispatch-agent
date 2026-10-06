"""Voice call dialogue tests. Run from the repo root: python -m tests.test_call

Plain asserts, no network, no audio and no API key: Claude is switched off, so the rule-based
parser reads what was "said", and address lookups are faked.
"""
from dispatch import call, llm

llm.USE_LLM = False


def parse(text, only_crew=None):
    try:
        return llm.parse_event(text, only_crew), None
    except Exception as e:
        return None, str(e)


def found(job):
    job.update(lat=51.045, lon=-114.06)
    return job


def not_found(job):
    return None


def talk(lines, locate=found):
    state = call.new_call()
    out = [call.step(state, line, parse, locate) for line in lines]
    return state, out


def test_clear_update_is_entered_straight_away():
    state, out = talk(["crew 4 called in sick"])
    reply, (kind, event, said, replace) = out[0]
    assert reply == "" and kind == "apply" and not replace
    assert event["event"] == "crew_out" and event["crew"] == 4 and said == "crew 4 called in sick"
    assert state["stage"] == "anything_else"


def test_asks_only_for_what_is_missing():
    _, out = talk(["someone called in sick", "four"])
    assert out[0][1] is None and "crew" in out[0][0].lower() and out[0][0].endswith("?")
    assert out[1][1][0] == "apply" and out[1][1][1]["crew"] == 4


def test_two_crews_named_asks_which_then_takes_that_one():
    _, out = talk(["crews 4 and 3 called in sick", "three"])
    assert "which one" in out[0][0].lower() and out[0][1] is None
    assert out[1][1][0] == "apply" and out[1][1][1]["crew"] == 3


def test_correction_right_after_replaces_the_update():
    _, out = talk(["crew 4 called in sick", "no, it's crew 3"])
    kind, event, _, replace = out[1][1]
    assert kind == "apply" and replace and event["crew"] == 3 and event["event"] == "crew_out"


def test_undo_takes_the_last_update_back_once():
    _, out = talk(["crew 4 called in sick", "undo that", "undo"])
    assert out[1] == ("Okay, I've taken that out. Anything else?", ("undo",))
    assert out[2] == ("There's nothing to undo. Anything else?", None)


def test_new_job_asks_where_until_it_can_place_it():
    calls = []

    def second_try(job):  # the first lookup fails, the one after the cross street works
        calls.append(1)
        return found(job) if len(calls) > 1 else None

    _, out = talk(["there's a sinkhole blocking traffic", "8th Ave SW and 4th St SW"], locate=second_try)
    assert out[0] == ("Where exactly is the sinkhole? Give me a cross street, like 8th Avenue and 4th Street "
                      "Southwest, or the community.", None)
    kind, event, _, _ = out[1][1]
    assert kind == "apply" and event["job"]["lat"] is not None


def test_unplaceable_job_goes_to_the_map_after_two_tries():
    _, out = talk(["there's a sinkhole blocking traffic", "somewhere downtown", "near the tower"], locate=not_found)
    assert out[2][1][0] == "place"
    assert "click the map where the sinkhole is" in call.place_reply(out[2][1][1])


def test_anything_else_then_goodbye_ends_the_call():
    _, out = talk(["crew 4 called in sick", "no"])
    assert out[1][1] == ("end",)
    _, out = talk(["crew 4 called in sick", "no that's all"])
    assert out[1][1] == ("end",)
    _, out = talk(["crew 4 called in sick", "yeah", "crew 2 is down a guy"])
    assert out[1] == ("Go ahead.", None) and out[2][1][1]["event"] == "crew_partial"


def test_cancel_while_asking_and_silence():
    _, out = talk(["someone called in sick", "never mind"])
    assert out[1] == ("Okay, I won't enter that. Anything else?", None)
    _, out = talk([None, "", None])
    assert out[0][0].startswith("Sorry") and out[2][1] == ("end",)


def test_spoken_replies():
    assert call.done_reply({"event": "crew_out", "crew": 4}, "6 jobs moved.") == \
        "Got it. Crew 4 is out for the day. 6 jobs moved. Anything else?"
    job = {"event": "new_job", "job": {"label": "Sinkhole", "severity": 3, "address": "17 Ave SW and 14 St SW"}}
    assert call.done_reply(job, "Crew 3 (E) takes it at P 4.50. To make room, Parking sign in Inglewood "
                                "(P 1.25) was deferred.") == \
        ("Got it. Added an emergency priority sinkhole at 17 Ave SW and 14 St SW. Crew 3 takes it. To make room, "
         "Parking sign in Inglewood was deferred. Anything else?")
    assert "(0 safety)" in call.done_reply({"event": "crew_out", "crew": 4}, "6 deferred (0 safety).")
    assert call.statement({"event": "crew_partial", "crew": 2, "capacity": 0.5}) == \
        "Crew 2 is short-handed, at about 50%."
    job = {"event": "new_job", "job": {"label": "Sinkhole", "severity": 3, "address": "8th Ave SW"}}
    assert call.statement(job) == "Added an emergency priority sinkhole at 8th Ave SW."


def test_transcript_keeps_both_sides():
    state, _ = talk(["someone called in sick", "four"])
    assert [who for who, _ in state["turns"]] == ["agent", "you", "agent", "you", "agent"]
    assert state["turns"][0] == ("agent", call.GREETING)


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
