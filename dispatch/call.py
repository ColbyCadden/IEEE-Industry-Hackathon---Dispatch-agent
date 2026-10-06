"""The voice call: one spoken turn in, one spoken reply out, until the update is clear enough to enter.

Pure logic, no Streamlit and no audio: the dashboard transcribes what the supervisor said, calls
step(), speaks the reply, and carries out the action. The agent asks only for what's missing (which
crew, or where a new job is) and enters the update as soon as it's clear. "Undo" takes the last one
back, and "no, it was crew 3" right after an entry replaces it.

    state = new_call()
    reply, action = step(state, "someone called in sick", parse, locate)   # -> asks "Which crew…?"
    reply, action = step(state, "crew four", parse, locate)                # -> ("apply", event, said, False)
"""
import re
from typing import Callable

from dispatch.live import SEVERITY_LABELS

GREETING = "City Link dispatch. What's the update?"
NOT_HEARD = "Sorry, I didn't catch that. Could you say it again?"
GOODBYE = "Okay. Thanks, bye."
COMMON_LINES = [GREETING, NOT_HEARD, GOODBYE, "Go ahead."]  # made ahead of time so they play instantly
MAX_WHERE_TRIES = 2  # after this many failed address lookups, the supervisor places the job on the map
NUMBER_WORDS_ORDER = ("one", "two", "three", "four", "five", "six", "seven", "eight")
NUMBER_WORDS = set(NUMBER_WORDS_ORDER)  # a bare answer to "Which crew?"

_YES = re.compile(r"^(?:yes|yeah|yea|yep|yup|sure|ok(?:ay)?|go ahead)\b")
_NO = re.compile(r"^(?:no|nope|nah|wrong|not quite|incorrect|actually)\b[\s,.!-]*")
_CANCEL = re.compile(r"^(?:cancel|scratch that|never ?mind|forget it)\b")
_UNDO = re.compile(r"^(?:undo|take (?:that|it) back|scratch that|cancel that|remove that|delete that|"
                   r"never ?mind that)\b")
_BYE = re.compile(r"\b(?:bye|goodbye|that's all|that is all|that's it|that is it|nothing else|no more|"
                  r"hang up|end (?:the )?call|i'm done|we're done|all done)\b")

Parse = Callable[[str, "int | None"], "tuple[dict | None, str | None]"]  # (text, only_crew) -> (event, error)
Locate = Callable[[dict], "dict | None"]


def new_call() -> dict:
    """Fresh call state: what has been said about the current update, and where the talk is at."""
    state = {"turns": [("agent", GREETING)], "misses": 0, "last": None}
    _reset(state, "collecting")
    return state


def _reset(state: dict, stage: str) -> None:
    state.update(stage=stage, said=[], where_tries=0, only_crew=None, candidates=[], replacing=False)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").lower())).strip()


def _named_crew(said: str) -> int | None:
    """The one crew a reply names ("crew 3", "it's crew three", or just "3"), else None."""
    words = {w: i for i, w in enumerate(NUMBER_WORDS_ORDER, 1)}
    found = {int(n) if n.isdigit() else words[n]
             for n in re.findall(rf"\bcrew (\d+|{'|'.join(NUMBER_WORDS)})\b", said)}
    if not found and (said.isdigit() or said in NUMBER_WORDS):
        found = {int(said) if said.isdigit() else words[said]}
    return found.pop() if len(found) == 1 else None


def statement(event: dict) -> str:
    """The entered update in one short spoken sentence."""
    kind, crew = event.get("event"), event.get("crew")
    if kind == "crew_out":
        return f"Crew {crew} is out for the day."
    if kind == "crew_partial" and float(event.get("capacity", 0.5)) >= 1.0:
        return f"Crew {crew} is back at full strength."
    if kind == "crew_partial":
        return f"Crew {crew} is short-handed, at about {float(event.get('capacity', 0.5)):.0%}."
    job = event["job"]
    where = job.get("address") or str(job.get("community") or "").title()
    sev = SEVERITY_LABELS[job["severity"]]
    return f"Added {'an' if sev[0] in 'aeiou' else 'a'} {sev} priority {job['label'].lower()}" + \
        (f" at {where}." if where else ".")


def done_reply(event: dict, effect: str) -> str:
    """What the agent says once the dashboard has entered the update. Zone letters and priority
    scores ("Crew 3 (E) takes it at P 4.50") read well on screen but badly aloud, so they go."""
    effect = re.sub(r"\s*\((?:[A-Z]{1,2}|P \d+(?:\.\d+)?)\)|\s+at P \d+(?:\.\d+)?", "", effect or "").strip()
    return " ".join(x for x in ("Got it.", statement(event), effect, "Anything else?") if x)


def place_reply(event: dict) -> str:
    return (f"Got it. I couldn't find that spot, so click the map where the {event['job']['label'].lower()} is. "
            f"Anything else?")


def step(state: dict, heard: str | None, parse: Parse, locate: Locate) -> tuple[str, tuple | None]:
    """Take one thing the supervisor said. Returns (reply to speak, action or None).

    Actions: ("apply", event, said, replace) enter the update; ("place", event, said, replace) enter
    it once the supervisor clicks the map; replace=True means undo the last update first (it was a
    correction). ("undo",) takes the last update back; ("end",) hangs up after the reply. For apply
    and place the reply is "": the dashboard says what the update did (done_reply / place_reply).
    """
    reply, action = _step(state, heard, parse, locate)
    if heard:
        state["turns"].append(("you", heard))
    state["turns"].append(("agent", reply))
    return reply, action


def _step(state: dict, heard: str | None, parse: Parse, locate: Locate) -> tuple[str, tuple | None]:
    said = _clean(heard)
    if not said:
        state["misses"] += 1
        if state["misses"] >= 3:
            return "I'm not hearing anything, so I'll hang up. Type the update in the box if you need to.", ("end",)
        return NOT_HEARD, None
    state["misses"] = 0

    if _BYE.search(said) or (state["stage"] == "anything_else" and _NO.fullmatch(said)):
        return GOODBYE, ("end",)

    if state["stage"] == "anything_else":
        if _UNDO.match(said):
            if state["last"] is None:
                return "There's nothing to undo. Anything else?", None
            state["last"] = None
            return "Okay, I've taken that out. Anything else?", ("undo",)
        correction = _NO.sub("", said, count=1).strip() if _NO.match(said) else ""
        if correction and state["last"]:  # "no, it was crew 3": fix the update just entered
            _reset(state, "collecting")
            state.update(said=[state["last"]["said"], f"Correction: {heard.strip()}"], replacing=True,
                         only_crew=_named_crew(correction) if state["last"]["crew_update"] else None)
            return _understand(state, parse, locate)
        if _YES.match(said) and len(said.split()) <= 2:  # "yes" / "yeah sure": wait for the update itself
            _reset(state, "collecting")
            return "Go ahead.", None
        _reset(state, "collecting")
    elif _CANCEL.match(said):
        _reset(state, "anything_else")
        return "Okay, I won't enter that. Anything else?", None

    if _named_crew(said) in state["candidates"]:  # "which one first?" answered
        state["only_crew"] = _named_crew(said)
    state["said"].append(f"crew {said}" if said.isdigit() or said in NUMBER_WORDS else heard.strip())
    return _understand(state, parse, locate)


def _understand(state: dict, parse: Parse, locate: Locate) -> tuple[str, tuple | None]:
    """Read everything said about this update so far; ask for what's missing, or enter it."""
    text = ". ".join(state["said"])
    event, _ = parse(text, state["only_crew"])
    if event is None:
        return ("Sorry, I couldn't work that out. Tell me the crew number and what happened, "
                "or what the problem is and where."), None
    if event["event"] == "unclear":
        state["candidates"] = event.get("candidates") or []
        return event.get("question") or "Which crew is it?", None
    located = True
    if event["event"] == "new_job" and event["job"].get("lat") is None and not locate(event["job"]):
        state["where_tries"] += 1
        if state["where_tries"] <= MAX_WHERE_TRIES:
            label = event["job"]["label"].lower()
            return (f"Where exactly is the {label}? Give me a cross street, like 8th Avenue and 4th Street "
                    f"Southwest, or the community."), None
        located = False
    replace = state["replacing"]
    state["last"] = {"said": text, "crew_update": event["event"] != "new_job"} if located else None
    _reset(state, "anything_else")
    return "", ("apply" if located else "place", event, text, replace)
