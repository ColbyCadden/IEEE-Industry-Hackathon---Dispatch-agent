"""Score each ticket. The whole scoring method lives here and in weights.py.

P = weight + AGE_PER_DAY * days_open + PER_EXTRA_REPORT * (reports - 1)

To swap in a different scoring method: change WEIGHTS / SAFETY_TYPES in weights.py and
`priority()` (plus FORMULA, `explain()` and SCALE_MAX_P) below. Everything else, including the
dashboard's score text and the 0-10 display scale, reads from here.
"""
import warnings

import pandas as pd

from dispatch.weights import SAFETY_TYPES, SHORT_NAMES, WEIGHTS

AGE_PER_DAY = 0.25       # P added per day a ticket has waited
PER_EXTRA_REPORT = 0.5   # P added per duplicate report of the same problem
FORMULA = "P = type weight (0-3) + 0.25 x days open + 0.5 x extra reports"

# Display only: the dashboard shows P on a 0-10 scale. Planning always uses raw P.
SCALE_MAX_P = 5.0        # P at or above this shows as 10/10
SCALE_NOTE = (f"Priority out of 10 = P x {10 / SCALE_MAX_P:g}, capped at 10 "
              f"(P {SCALE_MAX_P:g} or more = 10). Higher = more urgent.")


def priority(weight, days_open, reports):
    """The score formula. Works on numbers or pandas Series."""
    return weight + AGE_PER_DAY * days_open + PER_EXTRA_REPORT * (reports - 1)


def explain(weight: float, days_open: int, reports: int) -> str:
    """How one ticket's P was built, in words."""
    extra = reports - 1
    p = priority(weight, days_open, reports)
    return (f"{weight:g} type weight + {AGE_PER_DAY:g} x {days_open} day{'s' if days_open != 1 else ''} open "
            f"+ {PER_EXTRA_REPORT:g} x {extra} extra report{'s' if extra != 1 else ''} = P {p:.2f}")


def priority_10(p: float) -> float:
    """P on the dashboard's 0-10 display scale."""
    return round(min(10.0, max(0.0, 10.0 * p / SCALE_MAX_P)), 1)


def score(df: pd.DataFrame, today="2026-08-28") -> pd.DataFrame:
    """Return df + days_open, weight, P, safety, type; rows with weight 0 are dropped."""
    out = df.copy()
    unknown = sorted(set(out["service_name"]) - set(WEIGHTS))
    if unknown:  # a new type would otherwise vanish from the plan without a trace
        warnings.warn(f"no weight for {unknown}; these tickets are treated as weight 0 and excluded")
    out["days_open"] = (pd.Timestamp(today) - out["requested_date"]).dt.days
    if (out["days_open"] < 0).any():
        raise ValueError(f"tickets dated after today ({today}): check the `today` argument")
    out["weight"] = out["service_name"].map(WEIGHTS).fillna(0)
    out = out[out["weight"] > 0].copy()
    out["P"] = priority(out["weight"], out["days_open"], out["reports"])
    out["safety"] = out["service_name"].isin(SAFETY_TYPES)
    out["type"] = out["service_name"].map(SHORT_NAMES).fillna(out["service_name"])
    return out.reset_index(drop=True)


if __name__ == "__main__":
    from dispatch.data_prep import load_clean_tickets

    scored = score(load_clean_tickets())
    print(f"Rows after dropping weight 0: {len(scored)}")
    top = scored.sort_values(["P", "requested_date", "id"], ascending=[False, True, True])
    with pd.option_context("display.width", 160):
        print(top.head(10)[["id", "type", "community", "days_open", "weight",
                            "reports", "P", "safety"]].to_string(index=False))
