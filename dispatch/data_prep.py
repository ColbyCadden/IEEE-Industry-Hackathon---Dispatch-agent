"""Load and clean the 311 sample into an open-ticket DataFrame."""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "311_dispatch_sample.csv"


def load_tickets(path: Path = DATA, open_only: bool = True) -> pd.DataFrame:
    """Return tickets with service_name, comm_name, requested_date, lon/lat, age_days."""
    raise NotImplementedError
