"""Agent language layer: parse a supervisor's crew update, write 8 a.m. / noon briefings.

Every LLM call has a non-LLM fallback so the demo works offline.
"""
import json
import logging
import os
import re
from pathlib import Path

MODEL = "claude-sonnet-5-5"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"  # copy .env.example to .env, add your key
CREWS = 8
USE_LLM = True  # set False to force the regex / template fallbacks

log = logging.getLogger(__name__)
_client = None

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_CREW_RE = re.compile(r"\bcrew\s*(?:#|no\.?|number)?\s*(\d+|" + "|".join(_NUMBER_WORDS) + r")\b", re.I)
_PARTIAL_RE = re.compile(r"\bdown a (?:guy|person|man|worker)\b|\bshort\w*|\bhalf\b", re.I)
_OUT_RE = re.compile(r"\b(?:sick|out|off|down)\b", re.I)


def _event(kind: str, crew: int | None = None, capacity: float = 1.0, question: str | None = None) -> dict:
    return {"event": kind, "crew": crew, "capacity": capacity, "question": question}


def regex_parse_event(text: str) -> dict:
    """Rule-based parse of a crew update. Partial phrases are checked before out/sick/down."""
    m = _CREW_RE.search(text or "")
    if not m:
        return _event("unclear", question="Which crew is affected?")
    raw = m.group(1).lower()
    crew = _NUMBER_WORDS.get(raw) or int(raw)
    if not 1 <= crew <= CREWS:
        return _event("unclear", question=f"There is no crew {crew} today (crews 1-{CREWS}). Which crew is affected?")
    if _PARTIAL_RE.search(text):
        return _event("crew_partial", crew, 0.5)
    if _OUT_RE.search(text):
        return _event("crew_out", crew, 0.0)
    return _event("unclear", crew,
                  question=f"What's happening with crew {crew}: out for the day, or short-handed?")


def load_env(path: Path = ENV_FILE) -> None:
    """Copy KEY=value lines from the repo's .env into os.environ. Variables already set win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if value:  # a blank placeholder like ANTHROPIC_API_KEY= is ignored
            os.environ.setdefault(key.strip(), value)


def has_api_key() -> bool:
    load_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _ask_claude(system: str, user: str, max_tokens: int, output_format: dict | None = None) -> str:
    """One Claude call; returns the reply text or raises on any problem (caller falls back)."""
    global _client
    if _client is None:
        if not has_api_key():
            raise RuntimeError(f"no ANTHROPIC_API_KEY (add it to {ENV_FILE.name})")
        import anthropic  # imported here so a missing package just means "use the fallback"
        _client = anthropic.Anthropic(timeout=20.0, max_retries=1)
    output_config = {"effort": "low"}
    if output_format:
        output_config["format"] = output_format
    response = _client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        thinking={"type": "between_tools"},  # no thinking pass: short, fast answers
        output_config=output_config,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("empty reply")
    return text


_EVENT_SYSTEM = (
    "You are a dispatch intake assistant for City of Calgary road crews. Read the crew update and "
    'return ONLY a JSON object matching {"event": "crew_out"|"crew_partial"|"unclear", '
    '"crew": int or null, "capacity": float 0-1, "question": string or null}.\n'
    f"Crews are numbered 1-{CREWS}.\n"
    '- "crew_out": the crew can do no more work today. A crew that "called in sick", "is out", '
    '"is off" or "is done" counts as fully out unless the message says only some workers are missing. '
    "capacity 0, question null.\n"
    '- "crew_partial": the message says the crew is short-handed rather than fully out. Estimate '
    "capacity from what they said (e.g. one of four workers missing -> 0.75, half the crew -> 0.5). "
    "question null.\n"
    '- "unclear": the crew number isn\'t stated, isn\'t 1-8, or the message says nothing about the '
    "crew's availability. capacity 1, and question is one short clarifying question (under 15 words)."
)
_EVENT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "event": {"type": "string", "enum": ["crew_out", "crew_partial", "unclear"]},
            "crew": {"type": ["integer", "null"]},
            "capacity": {"type": "number"},
            "question": {"type": ["string", "null"]},
        },
        "required": ["event", "crew", "capacity", "question"],
        "additionalProperties": False,
    },
}


def _valid_event(d: dict) -> dict:
    """Return a clean event dict or raise ValueError."""
    kind, crew, cap, question = d.get("event"), d.get("crew"), d.get("capacity"), d.get("question")
    if kind not in ("crew_out", "crew_partial", "unclear"):
        raise ValueError(f"bad event {kind!r}")
    if crew is not None and (isinstance(crew, bool) or not isinstance(crew, int) or not 1 <= crew <= CREWS):
        raise ValueError(f"bad crew {crew!r}")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 <= cap <= 1:
        raise ValueError(f"bad capacity {cap!r}")
    if question is not None and not isinstance(question, str):
        raise ValueError(f"bad question {question!r}")
    if kind != "unclear" and crew is None:
        raise ValueError(f"{kind} without a crew")
    if kind == "crew_out":
        cap, question = 0.0, None
    return _event(kind, crew, float(cap), question or None)


def llm_parse_event(text: str) -> dict:
    """Claude reads the crew update; on any error, silently returns regex_parse_event(text)."""
    try:
        reply = _ask_claude(_EVENT_SYSTEM, text or "", max_tokens=200, output_format=_EVENT_FORMAT)
        reply = reply.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return _valid_event(json.loads(reply))
    except Exception as e:
        log.info("llm_parse_event fell back to regex: %s: %s", type(e).__name__, e)
        return regex_parse_event(text)


def parse_event(text: str) -> dict:
    """{"event": "crew_out"|"crew_partial"|"unclear", "crew", "capacity", "question"}."""
    return llm_parse_event(text) if USE_LLM else regex_parse_event(text)


def _split_metrics(metrics: dict, when: str) -> tuple[dict, dict | None]:
    """Accept run.py's metrics.json bundle {"8am","fifo","noon","changes"} or one compute() dict."""
    if "8am" in metrics or "noon" in metrics:
        return metrics.get("noon" if when == "noon" else "8am", {}), metrics.get("fifo")
    return metrics, None


def _sick_crew(plan: dict, changes: dict | None, event: dict | None) -> int | None:
    if event and event.get("crew"):
        return event["crew"]
    if changes and changes.get("moved"):
        return changes["moved"][0]["from"]
    empty = [c["crew"] for c in plan["crews"] if not c["jobs"]]
    return empty[0] if len(empty) == 1 else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def template_briefing(plan: dict, metrics: dict, when: str = "8am", changes: dict | None = None,
                      event: dict | None = None) -> str:
    """Plain-English supervisor briefing built from the numbers, no LLM."""
    m, fifo = _split_metrics(metrics, when)
    jobs = [j for c in plan["crews"] for j in c["jobs"]]
    active = sum(1 for c in plan["crews"] if c["jobs"])
    n = m.get("n", len(jobs))
    safety = m.get("safety", sum(bool(j["safety"]) for j in jobs))

    if when != "noon":
        lines = [f"Good morning. Today's plan sends {_plural(active, 'crew')} to {_plural(n, 'job')}, "
                 f"and {safety} of them are safety tickets (potholes and missing or damaged signs)."]
        if fifo:
            gap = safety - fifo["safety"]
            lines.append(f"Working oldest-first would have covered only {fifo['safety']} safety tickets, "
                         f"so this plan clears {gap} more safety hazards with the same crews.")
        if jobs:
            top = max(jobs, key=lambda j: j["P"])
            crew = next(c for c in plan["crews"] if top in c["jobs"])
            lines.append(f"Highest priority is a {top['type'].lower()} in {top['community'].title()}, "
                         f"assigned to crew {crew['crew']} ({crew['zone']}).")
        lines.append("Report any crew changes here and I will replan.")
        return " ".join(lines)

    changes = changes or (metrics.get("changes") if isinstance(metrics, dict) else None) or {}
    moved = m.get("moved", len(changes.get("moved", [])))
    dropped = m.get("dropped", len(changes.get("dropped", [])))
    safety_dropped = m.get("safety_dropped")
    crew = _sick_crew(plan, changes, event)
    who = f"Crew {crew}" if crew else "One crew"
    partial = event and event.get("event") == "crew_partial"

    lines = [f"Noon update: {who} is {'short-handed' if partial else 'out'}, "
             f"so the plan now covers {_plural(n, 'job')} with {safety} safety tickets."]
    lines.append(f"{_plural(moved, 'job')} {'was' if moved == 1 else 'were'} moved to nearby crews "
                 f"and {_plural(dropped, 'lower-priority job')} {'was' if dropped == 1 else 'were'} "
                 f"deferred to tomorrow.")
    if safety_dropped == 0:
        lines.append("No safety tickets were dropped.")
    elif safety_dropped:
        lines.append(f"Heads up: {_plural(safety_dropped, 'safety ticket')} had to be deferred; "
                     f"consider overtime or a call-in to cover {'it' if safety_dropped == 1 else 'them'}.")
    return " ".join(lines)


def _briefing_facts(plan: dict, metrics: dict, when: str, changes: dict | None, event: dict | None) -> dict:
    """Compact summary of the plan and metrics: the only numbers Claude is allowed to use."""
    m, fifo = _split_metrics(metrics, when)
    jobs = [j for c in plan["crews"] for j in c["jobs"]]
    facts = {
        "crews_working": sum(1 for c in plan["crews"] if c["jobs"]),
        "jobs_assigned": m.get("n", len(jobs)),
        "safety_tickets_covered": m.get("safety", sum(bool(j["safety"]) for j in jobs)),
        "priority_points_covered": m.get("P"),
        "jobs_by_type": {t: sum(j["type"] == t for j in jobs) for t in sorted({j["type"] for j in jobs})},
    }
    if when != "noon":
        if fifo:
            facts["oldest_first_safety_tickets"] = fifo["safety"]
            facts["extra_safety_tickets_vs_oldest_first"] = facts["safety_tickets_covered"] - fifo["safety"]
        top = sorted(jobs, key=lambda j: j["P"], reverse=True)[:3]
        crew_of = {j["id"]: c for c in plan["crews"] for j in c["jobs"]}
        facts["top_jobs"] = [
            f"{j['type']} in {j['community'].title()} (crew {crew_of[j['id']]['crew']}, {crew_of[j['id']]['zone']} zone)"
            for j in top
        ]
        return facts

    changes = changes or (metrics.get("changes") if isinstance(metrics, dict) else None) or {}
    safety_dropped = m.get("safety_dropped")
    if safety_dropped is None and "dropped_jobs" in changes:
        safety_dropped = sum(bool(j["safety"]) for j in changes["dropped_jobs"])
    crew = _sick_crew(plan, changes, event)
    if event and event.get("event") == "crew_partial":
        what = f"crew {crew} is short-handed ({event.get('capacity', 0):.0%} capacity)"
    else:
        what = f"crew {crew} is out for the day" if crew else "one crew is out for the day"
    facts.update({
        "disruption": what,
        "jobs_moved_to_other_crews": m.get("moved", len(changes.get("moved", []))),
        "jobs_deferred_to_tomorrow": m.get("dropped", len(changes.get("dropped", []))),
        "safety_tickets_deferred": "unknown" if safety_dropped is None else safety_dropped,
        "deferred_job_types": [j["type"] for j in changes.get("dropped_jobs", [])],
    })
    return facts


_BRIEFING_SYSTEM = (
    "You write short spoken briefings for a City of Calgary Roads supervisor. Write 3-4 short, plain "
    "sentences, under 80 words in total: no markdown, no bullet points, no headers. Lead with what "
    "matters most and skip anything the supervisor doesn't need. Use only numbers that appear in the data "
    "you are given; never invent or estimate a number. Safety tickets are potholes and missing or "
    "damaged signs."
)


def llm_briefing(plan: dict, metrics: dict, when: str = "8am", changes: dict | None = None,
                 event: dict | None = None) -> str:
    """Claude-written briefing from a compact summary; on any error, returns template_briefing(...)."""
    try:
        facts = _briefing_facts(plan, metrics, when, changes, event)
        if when != "noon":
            task = ("This is the 8 a.m. briefing. Say what the crews are doing today, and state how many "
                    "safety tickets the plan covers versus working oldest-first.")
        else:
            task = ("This is the noon replan briefing. Say what happened, how many jobs moved to other crews, "
                    "how many were deferred, and say explicitly whether any safety tickets were dropped.")
        user = f"{task}\n\nData:\n{json.dumps(facts, indent=1)}"
        return _ask_claude(_BRIEFING_SYSTEM, user, max_tokens=400)
    except Exception as e:
        log.info("llm_briefing fell back to template: %s: %s", type(e).__name__, e)
        return template_briefing(plan, metrics, when, changes, event)


def briefing(plan: dict, metrics: dict, when: str = "8am", changes: dict | None = None,
             event: dict | None = None) -> str:
    """3-4 plain-English sentences for a roads supervisor. Claude first, template fallback."""
    if USE_LLM:
        return llm_briefing(plan, metrics, when, changes, event)
    return template_briefing(plan, metrics, when, changes, event)


if __name__ == "__main__":
    import json
    from pathlib import Path

    out = Path("dispatch/outputs")
    metrics = json.loads((out / "metrics.json").read_text())
    plan_8am = json.loads((out / "plan_8am.json").read_text())
    plan_noon = json.loads((out / "plan_noon.json").read_text())
    event = json.loads((out / "event.json").read_text())
    logging.basicConfig(level=logging.WARNING, format="      [%(levelname)s] %(message)s")
    log.setLevel(logging.INFO)  # show our fallback notices, not every HTTP request

    checks = [
        ("Crew 4 called in sick", "crew_out", 4, 0.0),
        ("crew 2 is down a guy today", "crew_partial", 2, 0.5),
        ("Someone called in sick", "unclear", None, None),
        ("CREW 7 OUT", "crew_out", 7, 0.0),
        ("hey its crew 4, two guys are sick, we're done today", "crew_out", 4, 0.0),
    ]
    extras = ["crew 9 is sick", "crew 3 checking in", "Crew five is short two people", "crew 6 off today"]
    all_ok = True

    for USE_LLM in (True, False):
        print(f"\n===== USE_LLM = {USE_LLM} =====\n")
        print("8am:  ", briefing(plan_8am, metrics, "8am"))
        print()
        print("noon: ", briefing(plan_noon, metrics, "noon", metrics["changes"], event))
        print()
        print("noon (contract args only, flat compute() dict, no event):")
        print("      ", briefing(plan_noon, metrics["noon"], "noon", metrics["changes"]))
        print()
        ok = True
        for text, kind, crew, cap in checks:
            got = parse_event(text)
            if kind == "crew_partial" and USE_LLM:
                cap_ok = 0 < got["capacity"] < 1  # Claude estimates capacity; regex always says 0.5
            else:
                cap_ok = cap is None or got["capacity"] == cap
            good = got["event"] == kind and got["crew"] == crew and cap_ok
            ok &= good
            print(f"{'PASS' if good else 'FAIL'}  {text!r:54} -> {got}")
        print()
        for text in extras:
            print(f"      {text!r:54} -> {parse_event(text)}")
        print("\nALL PASS" if ok else "\nSOME FAILED")
        all_ok &= ok

    print("\nBOTH PATHS PASS" if all_ok else "\nA PATH FAILED")
