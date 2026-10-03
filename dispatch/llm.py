"""Agent language layer: parse a supervisor's crew update, write 8 a.m. / noon briefings.

Every LLM call has a non-LLM fallback so the demo works offline.
"""
import re

MODEL = "claude-sonnet-5-5"
CREWS = 8

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


def parse_event(text: str) -> dict:
    """{"event": "crew_out"|"crew_partial"|"unclear", "crew", "capacity", "question"}."""
    return regex_parse_event(text)


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


def briefing(plan: dict, metrics: dict, when: str = "8am", changes: dict | None = None,
             event: dict | None = None) -> str:
    """3-4 plain-English sentences for a roads supervisor. Template only for now."""
    return template_briefing(plan, metrics, when, changes, event)


if __name__ == "__main__":
    import json
    from pathlib import Path

    out = Path("dispatch/outputs")
    metrics = json.loads((out / "metrics.json").read_text())
    plan_8am = json.loads((out / "plan_8am.json").read_text())
    plan_noon = json.loads((out / "plan_noon.json").read_text())
    event = json.loads((out / "event.json").read_text())
    print("8am:  ", briefing(plan_8am, metrics, "8am"))
    print()
    print("noon: ", briefing(plan_noon, metrics, "noon", metrics["changes"], event))
    print()
    print("noon (contract args only, flat compute() dict, no event):")
    print("      ", briefing(plan_noon, metrics["noon"], "noon", metrics["changes"]))
    print()
    checks = [
        ("Crew 4 called in sick", "crew_out", 4, 0.0),
        ("crew 2 is down a guy today", "crew_partial", 2, 0.5),
        ("Someone called in sick", "unclear", None, None),
        ("CREW 7 OUT", "crew_out", 7, 0.0),
    ]
    extras = ["crew 9 is sick", "crew 3 checking in", "Crew five is short two people", "crew 6 off today"]
    ok = True
    for text, kind, crew, cap in checks:
        got = parse_event(text)
        good = got["event"] == kind and got["crew"] == crew and (cap is None or got["capacity"] == cap)
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {text!r:32} -> {got}")
    print()
    for text in extras:
        print(f"      {text!r:32} -> {parse_event(text)}")
    print("\nALL PASS" if ok else "\nSOME FAILED")
