"""Score each ticket: P = weight + 0.25*days_open + 0.5*(reports - 1)."""
import pandas as pd

from dispatch.weights import SAFETY_TYPES, SHORT_NAMES, WEIGHTS


def score(df: pd.DataFrame, today="2026-08-28") -> pd.DataFrame:
    """Return df + days_open, weight, P, safety, type; rows with weight 0 are dropped."""
    out = df.copy()
    out["days_open"] = (pd.Timestamp(today) - out["requested_date"]).dt.days
    out["weight"] = out["service_name"].map(WEIGHTS).fillna(0)
    out = out[out["weight"] > 0].copy()
    out["P"] = out["weight"] + 0.25 * out["days_open"] + 0.5 * (out["reports"] - 1)
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
