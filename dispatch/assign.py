"""Assign scored tickets to C crews x K jobs, plus the FIFO baseline."""
import pandas as pd

CREWS = 8
JOBS_PER_CREW = 5


def assign_fifo(df: pd.DataFrame, crews: int = CREWS, k: int = JOBS_PER_CREW) -> pd.DataFrame:
    """Oldest-first baseline. Returns df with a `crew` column."""
    raise NotImplementedError


def assign_scored(df: pd.DataFrame, crews: int = CREWS, k: int = JOBS_PER_CREW) -> pd.DataFrame:
    """Priority-based assignment. Returns df with a `crew` column."""
    raise NotImplementedError
