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
# Who produced the last parse / briefing: "claude", or "fallback: <reason>". Lets the UI say
# honestly whether Claude answered or the regex/template did.
last_source = {"parse": None, "briefing": None}

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_WORDNUM = {**_NUMBER_WORDS, "zero": 0, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
            "a": 1, "an": 1, "couple": 2, "few": 3}
CREW_SIZE = 4  # people per crew, assumed when an update counts people ("two guys out" -> 2 of 4 = 50%)

_NUM = r"\d{1,2}|" + "|".join(k for k in _WORDNUM if k not in ("a", "an", "couple", "few"))
_CREW_WORD = r"(?:crews?|teams?|units?|squads?|trucks?|groups?)"
_CREW_ONE = re.compile(rf"\b{_CREW_WORD}\s*(?:#|no\.?|number|num)?\s*({_NUM})\b", re.I)
_CREW_C = re.compile(r"\bc\s?-?(\d{1,2})\b", re.I)  # "C5"
_CREW_BARE = re.compile(rf"^\W*#?({_NUM})\b(?=\s*(?:is|are|'s|called|has|went|out|off|sick|short|down)\b)", re.I)
_LIST_MORE = re.compile(
    rf"\s*(?:,|&|/|\+|and|or)\s*(?:{_CREW_WORD}\s*(?:#|no\.?|number)?\s*)?({_NUM})\b"
    r"(?!\s*(?:guys?|people|persons?|men|workers?|members?|operators?|drivers?|%|percent|of\b))", re.I)

_PEOPLE = r"(?:guys?|people|persons?|men|man|workers?|members?|operators?|drivers?|labou?rers?|staff|hands)"
_CLAUSE_SPLIT = re.compile(
    r"[;.!?\n]+|\b(?:but|while|however|though|although|except)\b|[,&]?\s*\band\b\s+(?=" + _CREW_WORD + r"\s*#?\d|c\s?-?\d)"
    r"|,\s*(?=" + _CREW_WORD + r"\s*#?\w|c\s?-?\d)", re.I)

_NEG = r"(?:not|isn'?t|aren'?t|wasn'?t|weren'?t|no longer|never|doesn'?t|don'?t)"
_OUT = (r"(?:sick|ill|flu|covid|injur\w*|hurt|out|off|absent|away|gone|stranded|stuck|pulled|reassigned|"
        r"diverted|unavailable|no[- ]?show|on (?:leave|vacation|holiday)|vacation|day off|time off|"
        r"called off|break(?:s|ing)? down|broke(?:n)?[- ]?down|breakdown|accident|crash\w*|"
        r"(?:won'?t|will not|can'?t|cannot|not|isn'?t|aren'?t)\s+(?:be\s+)?(?:make it|coming|here|working|available|showing|in)|"
        r"done (?:for )?(?:the day|today)|finished (?:for )?(?:the day|today)|wrapped up for|"
        r"left early|leaving early|went home|going home|sent home|down|in the shop|at the shop|under repair|in for repair\w*|out of service)")
_PARTIAL = (r"(?:short[- ]?handed|short[- ]?staffed|under[- ]?staffed|half(?: the| a)?(?: crew| staff| team)?|"
            r"partial\w*|reduced|skeleton|limited)")
_OUT_DAY = re.compile(r"\b(?:done|finished|wrapped up)\s+(?:for\s+)?(?:the day|today)\b|\b(?:sent|went|going)\s+home\b|"
                      r"\b(?:left|leaving)\s+early\b")
_OK_STRONG = re.compile(r"\b(?:is back|are back|back (?:at|on|to) work|back now|back today|back in|"
                        r"made it|showed up|arrived|recovered|fully staffed|full strength|at full)\b")
_OK_WEAK = re.compile(r"\b(?:all good|all set|fine|ok|okay|good to go|available|good)\b")
_LATE = re.compile(r"\b(?:late|delayed|running behind|behind schedule)\b")
_ABSENT = r"(?:sick|ill|flu|covid|injured|hurt|lost|losing|vacation|leave|out|off|down|absent|missing|short|away|gone|called|unavailable|home)"
_PRESENT = r"(?:here|in|working|available|present|showed|showing|on shift|staffed|coming)"
_NOBODY = re.compile(r"\b(?:no ?one|nobody|none of (?:them|the guys|the crew))\b[^.;]*\b(?:showed|came|here|in|arrived|available|working)\b")
_NO_CREW = re.compile(r"\bno\s+crew\b")
_PRESENT_N = re.compile(rf"\b(?:only|just|with)\s+({_NUM}|an?)\s+{_PEOPLE}\b")
_COUNT_BARE = re.compile(rf"\b({_NUM})\s+(?:of\s+(?:the\s+)?crew\s+)?(?:are\s+|is\s+|were\s+)?(?:out|sick|off|down|absent|missing|away|gone)\b")
_PCT = re.compile(r"(\d{1,3})\s*(?:%|percent)")
_FRAC = re.compile(rf"\b({_NUM})\s*(?:of|out of|/)\s*({_NUM})\b")
_COUNT = re.compile(rf"\b(a couple of|couple of|a few|few|{_NUM}|an?)\s+(?:more\s+)?{_PEOPLE}\b")


def _event(kind: str, crew: int | None = None, capacity: float = 1.0, question: str | None = None,
           candidates: list | None = None) -> dict:
    ev = {"event": kind, "crew": crew, "capacity": capacity, "question": question}
    if candidates:
        ev["candidates"] = candidates  # crews the message mentions; the UI offers them as one-click answers
    return ev


def _num(tok: str) -> int | None:
    tok = tok.lower().strip()
    if tok.isdigit():
        return int(tok)
    return _WORDNUM.get(tok.removeprefix("a ").removesuffix(" of")) if tok else None


def crews_mentioned(text: str) -> list[int]:
    """Every crew number the text names ('crew 4', 'crews 2 and 6', 'C5'), in order, no duplicates."""
    return _crews_and_text(text or "")[0]


def _crews_and_text(clause: str) -> tuple[list[int], str]:
    """(crew numbers named in the clause, the clause with those mentions replaced by 'CREW')."""
    found: list[int] = []
    out, pos = [], 0
    for m in sorted([*_CREW_ONE.finditer(clause), *_CREW_C.finditer(clause)], key=lambda m: m.start()):
        if m.start() < pos:
            continue
        found.append(_num(m.group(1)))
        end = m.end()
        while True:  # "crews 2, 4 and 6"
            more = _LIST_MORE.match(clause, end)
            if not more:
                break
            found.append(_num(more.group(1)))
            end = more.end()
        out.append(clause[pos:m.start()])
        out.append(" CREW ")
        pos = end
    out.append(clause[pos:])
    text = "".join(out)
    if not found:
        b = _CREW_BARE.match(clause)
        if b:
            found.append(_num(b.group(1)))
            text = "CREW " + clause[b.end():]
    return list(dict.fromkeys(n for n in found if n is not None)), text


def _status(text: str):
    """What one clause says about its crew: ('out',) | ('partial', cap) | ('ok',) | ('late',) | None."""
    t = text.lower().replace("’", "'")
    has_out = re.search(rf"\b{_OUT}\b", t)
    has_partial = re.search(rf"\b{_PARTIAL}\b", t)
    if re.search(rf"\b{_NEG}\s+(?:\w+\s+){{0,2}}?(?:{_OUT}|{_PARTIAL})\b", t) and not re.search(
            r"\b(?:won'?t|can'?t|cannot|not|isn'?t|aren'?t)\s+(?:be\s+)?(?:make it|coming|here|working|available|showing|in)\b", t):
        return ("ok",)  # "not sick", "no longer out", "isn't short-handed"
    if _NOBODY.search(t) or _NO_CREW.search(t):
        return ("out",)
    if _OK_STRONG.search(t):
        return ("ok",)
    m = _PCT.search(t)
    if m:
        pct = min(int(m.group(1)), 100)
        return ("ok",) if pct >= 100 else ("out",) if pct == 0 else ("partial", round(pct / 100, 2))
    if _OUT_DAY.search(t):
        return ("out",)
    rest = t
    m = _FRAC.search(t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        rest = t[:m.start()] + " " + t[m.end():]
        if a is not None and b and 2 <= b <= 12 and a <= b:
            absent = bool(re.search(rf"\b{_ABSENT}\b", rest)) and not re.search(rf"\b(?:only|just|has|have|got|{_PRESENT})\b", rest)
            present = b - a if absent else a
            return ("ok",) if present >= b else ("out",) if present <= 0 else ("partial", round(present / b, 2))
    rest = re.sub(r"\b(?:down|reduced|cut|left)\s+(?:to|with)\b", "with", rest)
    m = _PRESENT_N.search(rest)
    if m and not re.search(rf"\b{_ABSENT}\b", rest[:m.start()] + rest[m.end():]):
        n = _num(m.group(1))
        if n is not None:
            cap = min(n, CREW_SIZE) / CREW_SIZE
            return ("ok",) if cap >= 1 else ("partial", round(cap, 2))
    m = _COUNT.search(rest) or _COUNT_BARE.search(rest)
    if m and re.search(rf"\b{_ABSENT}\b", rest):
        n = _num(m.group(1))
        if n is not None:
            cap = max(0.0, (CREW_SIZE - n) / CREW_SIZE)
            return ("out",) if cap <= 0 else ("partial", round(cap, 2))
    if has_partial:
        return ("partial", 0.5)
    if has_out:
        return ("out",)
    if _OK_WEAK.search(t):
        return ("ok",)
    if _LATE.search(t):
        return ("late",)
    return None


def _pairs(text: str) -> tuple[list[tuple[int, tuple]], list[tuple]]:
    """[(crew, status)] for every crew the message gives a status to, plus statuses with no crew."""
    clauses = []
    for part in _CLAUSE_SPLIT.split(text or ""):
        if not part or not part.strip():
            continue
        crews, plain = _crews_and_text(part)
        clauses.append([crews, _status(plain)])
    # a clause naming crews but no status takes the next status ("crew 4 and crew 5 are both out")
    for i, (crews, st) in enumerate(clauses):
        if crews and st is None:
            nxt = next((c[1] for c in clauses[i + 1:] if c[1] is not None), None)
            clauses[i][1] = nxt or next((c[1] for c in reversed(clauses[:i]) if c[1] is not None), None)
    # a status with no crew ("two guys called in sick") attaches to the nearest crew without a status
    orphans = []
    for i, (crews, st) in enumerate(clauses):
        if st is not None and not crews:
            names = [c for c in clauses if c[0]]
            if len({n for c in names for n in c[0]}) == 1:
                clauses[i][0] = list(names[0][0])
            else:
                orphans.append(st)
    pairs = [(n, st) for crews, st in clauses if st is not None for n in crews]
    return pairs, orphans


def regex_parse_event(text: str, only_crew: int | None = None) -> dict:
    """Rule-based parse of a crew update (used offline, and to sanity-check Claude's answer).

    Handles crew numbers as digits, words, 'C5' or lists; out / short-handed / fractions ('3 of 5'),
    percentages, head-counts ('two guys sick'), negations ('not sick', 'back'), and several crews in
    one message (asks which one, since the planner replans one crew at a time).
    """
    text = (text or "").strip()
    pairs, orphans = _pairs(text)
    if only_crew is not None:
        pairs = [p for p in pairs if p[0] == only_crew]
    valid = [(n, st) for n, st in pairs if 1 <= n <= CREWS]
    bad = sorted({n for n, _ in pairs if not 1 <= n <= CREWS})
    final = dict(valid)  # later statements about the same crew override earlier ones ("sick ... fine now")
    live = [(n, st) for n, st in final.items() if st[0] != "ok"]
    crews = list(dict.fromkeys(n for n, _ in live))
    mentioned = [n for n in crews_mentioned(text) if 1 <= n <= CREWS]

    if len(crews) > 1:
        names = ", ".join(map(str, crews[:-1])) + f" and {crews[-1]}"
        return _event("unclear", None, 1.0, f"That mentions crews {names}. I replan one crew at a time: which one first?",
                      crews)
    if not crews:
        if valid:  # only "is back / fine" statements: nothing to replan
            return _event("crew_partial", valid[-1][0], 1.0)
        if bad:
            return _event("unclear", question=f"There is no crew {bad[0]} today (crews 1-{CREWS}). Which crew is affected?")
        if not mentioned:
            return _event("unclear", question="Which crew is affected?")
        return _event("unclear", mentioned[0], 1.0,
                      f"What's happening with crew {mentioned[0]}: out for the day, or short-handed?")
    crew = crews[0]
    st = final[crew]
    if st[0] == "out":
        return _event("crew_out", crew, 0.0)
    if st[0] == "partial":
        return _event("crew_partial", crew, float(st[1]))
    return _event("unclear", crew, 1.0, f"Is crew {crew} out for the day, or just running late?")


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
    """True only for something shaped like a real key; a leftover placeholder counts as no key."""
    load_env()
    return os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-ant-")


def _ask_claude(system: str, user: str, max_tokens: int, output_format: dict | None = None) -> str:
    """One Claude call; returns the reply text or raises on any problem (caller falls back)."""
    global _client
    if _client is None:
        if not has_api_key():
            raise RuntimeError(f"no ANTHROPIC_API_KEY (add it to {ENV_FILE.name})")
        import anthropic  # imported here so a missing package just means "use the fallback"
        _client = anthropic.Anthropic(timeout=10.0, max_retries=1)
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
    f"Crews are numbered 1-{CREWS}. A crew has {CREW_SIZE} people. Messages may be messy: typos, "
    "number words (crew four), 'C5', slang, or extra chatter.\n"
    '- "crew_out": the crew can do no more work today: called in sick, out, off, done for the day, left '
    "early, no-show, vacation, truck broke down or in the shop, pulled to an emergency, 'down'. "
    "Counts as fully out unless the message says only some workers are missing. capacity 0, question null.\n"
    '- "crew_partial": some workers are missing or the crew is short-handed. capacity = people '
    f"available / {CREW_SIZE} (one person out -> 0.75, two -> 0.5, '3 of 5 here' -> 0.6, 'half the crew' -> "
    "0.5, '75%' -> 0.75). Short-handed with no detail -> 0.5. If the message says a crew is back, fully "
    "staffed, or not sick, use crew_partial with capacity 1. question null.\n"
    '- "unclear": the crew number isn\'t stated, isn\'t 1-8, the message names MORE THAN ONE crew that has '
    "a problem (the planner replans one crew at a time), or it says nothing about availability (or only "
    "that a crew is running late). capacity 1, and question is one short clarifying question (under 15 words).\n"
    "Only use a crew number that appears in the message."
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


_parse_cache: dict[str, dict] = {}  # Claude's answer per message, so a repeated update costs no API call


def _named_crews(text: str) -> list[int]:
    return [n for n in crews_mentioned(text) if 1 <= n <= CREWS]


def llm_parse_event(text: str, only_crew: int | None = None) -> dict:
    """Claude reads the crew update; on any error, silently returns regex_parse_event(text).

    The rules run first. They settle the cases that need no model (a crew was picked for you, or the
    message names several crews and the supervisor must choose), and afterwards they check Claude:
    an answer naming a crew the message never mentions is thrown away.
    """
    rules = regex_parse_event(text, only_crew)
    if only_crew is not None or (rules["event"] == "unclear" and len(rules.get("candidates") or []) > 1):
        last_source["parse"] = "rules: no model needed"
        return rules
    try:
        key = " ".join((text or "").lower().split())
        if key in _parse_cache:
            last_source["parse"] = "claude"
            return dict(_parse_cache[key])
        reply = _ask_claude(_EVENT_SYSTEM, text or "", max_tokens=200, output_format=_EVENT_FORMAT)
        reply = reply.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        event = _valid_event(json.loads(reply))
        named = _named_crews(text)
        if event["crew"] is not None and named and event["crew"] not in named:
            raise ValueError(f"Claude chose crew {event['crew']} but the message names {named}")
        _parse_cache[key] = event
        last_source["parse"] = "claude"
        return dict(event)
    except Exception as e:
        log.info("llm_parse_event fell back to regex: %s: %s", type(e).__name__, e)
        last_source["parse"] = f"fallback: {type(e).__name__}"
        return rules


def parse_event(text: str, only_crew: int | None = None) -> dict:
    """{"event": "crew_out"|"crew_partial"|"unclear", "crew", "capacity", "question"[, "candidates"]}.

    only_crew: the supervisor already picked which crew to replan (after "which one first?").
    """
    if USE_LLM:
        return llm_parse_event(text, only_crew)
    last_source["parse"] = "fallback: USE_LLM is off"
    return regex_parse_event(text, only_crew)


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
                 f"deferred (not done today).")
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
        "jobs_deferred_not_done_today": m.get("dropped", len(changes.get("dropped", []))),
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
        text = _ask_claude(_BRIEFING_SYSTEM, user, max_tokens=400)
        last_source["briefing"] = "claude"
        return text
    except Exception as e:
        log.info("llm_briefing fell back to template: %s: %s", type(e).__name__, e)
        last_source["briefing"] = f"fallback: {type(e).__name__}"
        return template_briefing(plan, metrics, when, changes, event)


def briefing(plan: dict, metrics: dict, when: str = "8am", changes: dict | None = None,
             event: dict | None = None) -> str:
    """3-4 plain-English sentences for a roads supervisor. Claude first, template fallback."""
    if USE_LLM:
        return llm_briefing(plan, metrics, when, changes, event)
    last_source["briefing"] = "fallback: USE_LLM is off"
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
        ("crew 2 is down a guy today", "crew_partial", 2, 0.75),
        ("Someone called in sick", "unclear", None, None),
        ("CREW 7 OUT", "crew_out", 7, 0.0),
        ("hey its crew 4, two guys are sick, we're done today", "crew_out", 4, 0.0),
    ]
    extras = ["crew 9 is sick", "crew 3 checking in", "Crew five is short two people", "crew 6 off today",
              "C5 is out", "crew 3 is not sick", "crew 2 only has 3 of 5 people", "crews 2 and 6 called in sick"]
    all_ok = True

    print(f"API key usable: {has_api_key()}  (without one, the USE_LLM=True run below is really the fallback)")
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
        print(f"answered by: parse={last_source['parse']}  briefing={last_source['briefing']}")
        all_ok &= ok

    print("\nBOTH PATHS PASS" if all_ok else "\nA PATH FAILED")
