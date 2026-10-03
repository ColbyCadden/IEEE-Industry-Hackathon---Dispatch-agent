"""Service-type priority weights (safety types highest) and disruption overrides."""

BASE_WEIGHTS: dict[str, float] = {}      # service_name keyword -> weight
BLIZZARD_WEIGHTS: dict[str, float] = {}  # overrides applied during a blizzard


def weight_for(service_name: str, blizzard: bool = False) -> float:
    raise NotImplementedError
