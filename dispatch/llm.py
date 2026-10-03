"""Claude-written supervisor briefings for 8 a.m. and noon."""
import anthropic

MODEL = "claude-sonnet-5-5"


def briefing(metrics: dict, when: str, event: dict | None = None) -> str:
    """Return a short plain-English briefing. Falls back to a template if no API key."""
    raise NotImplementedError
