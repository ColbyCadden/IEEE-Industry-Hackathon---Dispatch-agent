"""Score each ticket: type weight + age, plus an optional same-community bonus."""
import pandas as pd


def score_tickets(df: pd.DataFrame, blizzard: bool = False) -> pd.DataFrame:
    """Return df with a `score` column, sorted descending."""
    raise NotImplementedError
