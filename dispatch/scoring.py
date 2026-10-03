"""Score each ticket: P = weight + 0.25*days_open + 0.5*(reports - 1)."""
import pandas as pd


def score(df: pd.DataFrame, today="2026-08-28", weights=None, safety_types=None,
          short_names=None) -> pd.DataFrame:
    """Return df + days_open, weight, P, safety, type; rows with weight 0 are dropped.

    weights/safety_types/short_names default to the tables in dispatch.weights.
    """
    if weights is None or safety_types is None or short_names is None:
        from dispatch.weights import SAFETY_TYPES, SHORT_NAMES, WEIGHTS
        weights = WEIGHTS if weights is None else weights
        safety_types = SAFETY_TYPES if safety_types is None else safety_types
        short_names = SHORT_NAMES if short_names is None else short_names

    out = df.copy()
    out["days_open"] = (pd.Timestamp(today) - out["requested_date"]).dt.days
    out["weight"] = out["service_name"].map(weights).fillna(0)
    out = out[out["weight"] > 0].copy()
    out["P"] = out["weight"] + 0.25 * out["days_open"] + 0.5 * (out["reports"] - 1)
    out["safety"] = out["service_name"].isin(safety_types)
    out["type"] = out["service_name"].map(short_names).fillna(out["service_name"])
    return out.reset_index(drop=True)


if __name__ == "__main__":
    from dispatch.data_prep import load_clean_tickets

    # TEMPORARY test table: weights.py is still a placeholder. Not the real table.
    tmp_weights = {
        "Roads - Pothole Maintenance": 3,
        "Roads - Signs - Missing - Damaged": 3,
        "Roads - Signs - Traffic and Roadmarking": 3,
        "Roads - Debris on Street/Sidewalk/Boulevard": 2,
        "Roads - Signs - Parking": 1,
        "WRS - Waste - Residential": 1,
        "WRS - Commercial Collection Services": 1,
        "WRS - New Service - Carts": 1,
    }
    tmp_safety = {
        "Roads - Pothole Maintenance",
        "Roads - Signs - Missing - Damaged",
        "Roads - Signs - Traffic and Roadmarking",
        "Roads - Debris on Street/Sidewalk/Boulevard",
    }
    tmp_short = {name: name.split(" - ", 1)[-1] for name in tmp_weights}

    scored = score(load_clean_tickets(), weights=tmp_weights,
                   safety_types=tmp_safety, short_names=tmp_short)
    print(f"Rows after dropping weight 0: {len(scored)}")
    top = scored.sort_values(["P", "requested_date", "id"], ascending=[False, True, True])
    with pd.option_context("display.width", 160):
        print(top.head(10)[["id", "type", "community", "days_open", "weight",
                            "reports", "P", "safety"]].to_string(index=False))
