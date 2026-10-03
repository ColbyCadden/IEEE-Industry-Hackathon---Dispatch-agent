"""Apply one disruption (blizzard or sick crew) and reassign."""
import pandas as pd


def apply_event(event: dict) -> dict:
    """Normalize an event, e.g. {"type": "sick_crew", "crew": 3} or {"type": "blizzard"}."""
    raise NotImplementedError


def replan(tickets: pd.DataFrame, plan_8am: pd.DataFrame, event: dict) -> pd.DataFrame:
    """Return the noon plan after the disruption."""
    raise NotImplementedError
