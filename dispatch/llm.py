"""Claude agent layer: parse a supervisor's disruption message and write 8 a.m. / noon briefings.

Claude is required. Set ANTHROPIC_API_KEY in the environment or in a `.env` file at the repo root.
Crews are numbered 1..N everywhere.
"""
import json
import os
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-5-5"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_client: anthropic.Anthropic | None = None


class LLMUnavailable(RuntimeError):
    """Claude could not be reached (missing/invalid key or no network)."""


def _load_env() -> None:
    """Read KEY=VALUE lines from .env into os.environ without overriding existing vars."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def has_key() -> bool:
    _load_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not has_key():
            raise LLMUnavailable(
                "No Claude credentials found. Set ANTHROPIC_API_KEY in your environment "
                "or add `ANTHROPIC_API_KEY=...` to a .env file in the repo root."
            )
        _client = anthropic.Anthropic()
    return _client


def _call(system: str, user: str, output_format: dict | None = None) -> str:
    """One Claude request; returns the text of the reply."""
    output_config = {"effort": "low"}
    if output_format is not None:
        output_config["format"] = output_format
    try:
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable("Claude rejected the API key. Check ANTHROPIC_API_KEY.") from e
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable("Could not reach Claude. Check your internet connection.") from e

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's reply was cut off (max_tokens).")
    return "".join(b.text for b in response.content if b.type == "text").strip()


EVENT_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "event": {"type": "string", "enum": ["crew_out", "crew_partial", "unclear"]},
            "crew": {"type": ["integer", "null"]},
            "capacity": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["event", "crew", "capacity", "reason"],
        "additionalProperties": False,
    },
}


def parse_event(text: str, crews: int = 8) -> dict:
    """Turn a free-text field message into an event for replan.apply_event.

    Returns {"event": "crew_out" | "crew_partial" | "unclear", "crew": int | None,
    "capacity": float, "reason": str}. A missing or out-of-range crew number comes back
    as "unclear" (apply_event leaves the plan alone), with the reason saying why.
    """
    system = (
        "You read short messages sent to a City of Calgary 311 dispatch supervisor and decide "
        "how they change today's crew plan.\n"
        f"There are {crews} road crews, numbered 1 to {crews}.\n"
        "Event types:\n"
        '- "crew_out": a crew can do no more work today (sick, called in, truck broke down, '
        "won't make it). capacity = 0.\n"
        '- "crew_partial": a crew is working at reduced capacity (short-handed, leaving early, '
        "one truck down). capacity = the fraction of a normal day it can still do, between 0 and 1 "
        "(e.g. 2 of 4 workers -> 0.5, leaving at noon -> 0.5).\n"
        '- "unclear": the message is not about crew capacity, or you cannot tell which crew. '
        "capacity = 1.\n"
        "Set crew to the crew number exactly as the message states it, or null if none is given. "
        "In reason, write one short sentence explaining your reading."
    )
    data = json.loads(_call(system, text, EVENT_SCHEMA))

    event = {
        "event": data["event"],
        "crew": data.get("crew"),
        "capacity": min(max(float(data.get("capacity", 0.0)), 0.0), 1.0),
        "reason": data.get("reason", ""),
    }
    if event["event"] == "crew_out":
        event["capacity"] = 0.0
    if event["event"] != "unclear":
        if event["crew"] is None:
            event.update(event="unclear", reason=f"A crew is affected but the message doesn't say which (1-{crews}).")
        elif not 1 <= event["crew"] <= crews:
            event.update(event="unclear", reason=f"Crew {event['crew']} doesn't exist. Crews are numbered 1-{crews}.")
    return event


def _describe_event(event: dict | None) -> str:
    kind = (event or {}).get("event")
    if kind == "crew_out":
        return f"Crew {event.get('crew')} is out for the rest of the day."
    if kind == "crew_partial":
        return f"Crew {event.get('crew')} is down to {event.get('capacity', 0):.0%} capacity."
    return "No disruption."


def briefing(metrics: dict, when: str, event: dict | None = None) -> str:
    """Return a short plain-English supervisor briefing for `when` = "8am" or "noon"."""
    if when == "8am":
        focus = (
            "This is the 8 a.m. briefing. Say what the crews are tackling first and why, "
            "and how the plan beats sending the oldest tickets first (FIFO). "
            "P is total priority points covered; safety is the number of safety jobs covered."
        )
    else:
        focus = (
            "This is the noon replan briefing. Say what disrupted the morning plan, how many jobs "
            "moved to another crew or were dropped (and how many of those dropped were safety jobs), "
            "and what important work is still at risk. P is total priority points covered."
        )
    system = (
        "You write radio-style briefings for a City of Calgary Roads supervisor. "
        "Five sentences or fewer, plain text, no markdown, no headers, no bullet points. "
        "Use concrete numbers from the data. Do not invent numbers that are not in the data. "
        "Crews are numbered 1 and up."
    )
    user = (
        f"{focus}\n\n"
        f"Disruption: {_describe_event(event)}\n\n"
        f"Plan metrics (JSON):\n{json.dumps(metrics, sort_keys=True, default=str, indent=1)}"
    )
    return _call(system, user)
