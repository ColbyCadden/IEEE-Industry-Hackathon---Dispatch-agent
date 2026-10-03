"""Service-type priority weights (0..3), safety types, and short display labels.

3 = public-safety hazard, 2 = road hazard / obstruction, 1 = service or nuisance,
0 = not a field-crew job (excluded from dispatch).
"""

WEIGHTS = {
    "Roads - Pothole Maintenance": 3,                  # vehicle damage, cyclist and motorcyclist falls
    "Roads - Signs - Missing - Damaged": 3,            # a missing stop/yield sign is a direct crash risk
    "Roads - Signs - Traffic and Roadmarking": 2,      # faded markings/signals confuse drivers; sign still mostly there
    "Roads - Debris on Street/Sidewalk/Boulevard": 2,  # obstruction for cars, bikes, pedestrians; usually avoidable
    "Roads - Signs - Parking": 1,                      # enforcement/convenience issue, no safety impact
    "WRS - Waste - Residential": 1,                    # missed pickup: nuisance, next cycle fixes it
    "WRS - Commercial Collection Services": 1,         # contracted service issue, no public hazard
    "WRS - New Service - Carts": 1,                    # cart delivery request, schedulable any day
    "CFD - Provincial Licence Inspection - FHB": 0,    # fire-department inspection, not a field crew job
    "RSP - Home Services for Seniors Inquiry": 0,      # social-services inquiry, not a field crew job
}
SAFETY_TYPES = {name for name, w in WEIGHTS.items() if w == 3}
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

    counts = pd.read_csv("data/311_dispatch_sample.csv")["service_name"].value_counts()
    print(f"{'service_name':45} {'count':>5} {'weight':>6}  short")
    for name, n in counts.items():
        print(f"{name:45} {n:>5} {WEIGHTS.get(name, '??'):>6}  {SHORT_NAMES.get(name, '??')}")

    missing = set(counts.index) - set(WEIGHTS)
    assert len(counts) == 10, f"expected 10 service types in the CSV, found {len(counts)}"
    assert not missing, f"service types missing from WEIGHTS: {sorted(missing)}"
    assert all(w in (0, 1, 2, 3) for w in WEIGHTS.values()), "weights must be 0..3"
    assert SAFETY_TYPES == {n for n, w in WEIGHTS.items() if w == 3}, "SAFETY_TYPES must be the weight-3 types"
    assert set(SHORT_NAMES) == set(WEIGHTS), "SHORT_NAMES must cover exactly the WEIGHTS keys"
    print(f"\nOK: all {len(counts)} CSV service types present in WEIGHTS")
    print(f"SAFETY_TYPES: {sorted(SHORT_NAMES[n] for n in SAFETY_TYPES)}")
