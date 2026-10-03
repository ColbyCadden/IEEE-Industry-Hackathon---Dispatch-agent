"""Compare plans: priority covered, types served, jobs moved/dropped."""
import pandas as pd


def plan_metrics(plan: pd.DataFrame) -> dict:
    raise NotImplementedError


def diff_plans(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """Count jobs that changed crew, were dropped, or were added."""
    raise NotImplementedError
