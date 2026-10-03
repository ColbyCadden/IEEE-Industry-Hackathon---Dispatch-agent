"""Load and clean the 311 sample into an open-ticket DataFrame."""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "311_dispatch_sample.csv"


def load_clean_tickets(path=DATA) -> pd.DataFrame:
    """Return one row per open problem, deduped by service + location.

    Duplicate reports (same service_name at the same lat/lon rounded to 5
    decimals) collapse to the oldest ticket, with the group size in `reports`.
    """
    df = pd.read_csv(path, parse_dates=["requested_date"])
    df = df[df["status_description"] != "Closed"].copy()

    df["lat_key"] = df["latitude"].round(5)
    df["lon_key"] = df["longitude"].round(5)
    keys = ["service_name", "lat_key", "lon_key"]

    df = df.sort_values(["requested_date", "service_request_id"])
    df["reports"] = df.groupby(keys, dropna=False)["service_request_id"].transform("size")
    df = df.drop_duplicates(subset=keys, keep="first")

    df = df.rename(columns={
        "service_request_id": "id",
        "comm_name": "community",
        "latitude": "lat",
        "longitude": "lon",
    })
    cols = ["id", "service_name", "community", "requested_date", "lat", "lon", "reports"]
    return df[cols].reset_index(drop=True)


if __name__ == "__main__":
    raw = pd.read_csv(DATA)
    filtered = raw[raw["status_description"] != "Closed"]
    clean = load_clean_tickets()
    print(f"Total rows:            {len(raw)}")
    print(f"Rows after filtering:  {len(filtered)}")
    print(f"Unique problems:       {len(clean)}")
    print(f"Problems w/ reports>1: {(clean['reports'] > 1).sum()}")
