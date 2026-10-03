"""Service-type priority weights (0..3), safety types, and short display labels."""

WEIGHTS = {
    "Roads - Pothole Maintenance": 3,
    "Roads - Signs - Missing - Damaged": 3,
    "Roads - Signs - Traffic and Roadmarking": 2,
    "Roads - Debris on Street/Sidewalk/Boulevard": 2,
    "Roads - Signs - Parking": 1,
    "WRS - Waste - Residential": 1,
    "WRS - Commercial Collection Services": 1,
    "WRS - New Service - Carts": 1,
    "CFD - Provincial Licence Inspection - FHB": 0,
    "RSP - Home Services for Seniors Inquiry": 0,
}
SAFETY_TYPES = {"Roads - Pothole Maintenance", "Roads - Signs - Missing - Damaged"}
SHORT_NAMES = {
    "Roads - Pothole Maintenance": "Pothole",
    "Roads - Signs - Missing - Damaged": "Damaged sign",
    "Roads - Signs - Traffic and Roadmarking": "Traffic sign",
    "Roads - Debris on Street/Sidewalk/Boulevard": "Debris",
    "Roads - Signs - Parking": "Parking sign",
    "WRS - Waste - Residential": "Residential waste",
    "WRS - Commercial Collection Services": "Commercial waste",
    "WRS - New Service - Carts": "New carts",
    "CFD - Provincial Licence Inspection - FHB": "Licence inspection",
    "RSP - Home Services for Seniors Inquiry": "Seniors services",
}


if __name__ == "__main__":
    import pandas as pd

    csv_types = set(pd.read_csv("data/311_dispatch_sample.csv")["service_name"].unique())
    missing = csv_types - set(WEIGHTS)
    assert not missing, f"service types missing from WEIGHTS: {sorted(missing)}"
    assert SAFETY_TYPES <= set(WEIGHTS), "SAFETY_TYPES must be keys of WEIGHTS"
    assert set(SHORT_NAMES) == set(WEIGHTS), "SHORT_NAMES must cover exactly the WEIGHTS keys"
    assert all(0 <= w <= 3 for w in WEIGHTS.values()), "weights must be in 0..3"
    print(f"OK: all {len(csv_types)} CSV service types present in WEIGHTS")
