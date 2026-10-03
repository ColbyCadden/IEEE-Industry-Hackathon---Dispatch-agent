"""Weight sensitivity check. Run from the repo root: python -m tests.sensitivity

For each service type, move its weight by +1 and -1 (clamped to 0..3), rebuild both
plans, and compare safety tickets covered by the agent vs FIFO. "Safety" stays the
baseline SAFETY_TYPES throughout, so every row is measured on the same yardstick.
"""
import sys
from unittest import mock


def run(weights_override=None):
    """Return (agent metrics, fifo metrics) with WEIGHTS temporarily overridden."""
    import dispatch.scoring as scoring
    from dispatch.assign import make_plan
    from dispatch.data_prep import load_clean_tickets
    from dispatch.metrics import compute

    with mock.patch.dict(scoring.WEIGHTS, weights_override or {}):
        scored = scoring.score(load_clean_tickets())
    return (compute(make_plan(scored, order="priority")),
            compute(make_plan(scored, order="fifo")))


def main() -> int:
    try:
        from dispatch.weights import SHORT_NAMES, WEIGHTS
        base_agent, base_fifo = run()
    except (ImportError, NotImplementedError) as e:
        print(f"SKIPPED: {getattr(e, 'name', None) or e}")
        return 0

    header = f"{'change':32} {'agent':>6} {'fifo':>5} {'margin':>7}"
    print(header)
    print("-" * len(header))
    print(f"{'baseline (no change)':32} {base_agent['safety']:>6} {base_fifo['safety']:>5} "
          f"{base_agent['safety'] - base_fifo['safety']:>+7}")

    margins = []
    for name, w in WEIGHTS.items():
        for step in (+1, -1):
            new = min(3, max(0, w + step))
            label = f"{SHORT_NAMES.get(name, name)} {w}->{new}"
            if new == w:
                print(f"{label:32} {'(clamped, no change)':>20}")
                continue
            agent, fifo = run({name: new})
            margin = agent["safety"] - fifo["safety"]
            margins.append(margin)
            print(f"{label:32} {agent['safety']:>6} {fifo['safety']:>5} {margin:>+7}")

    print(f"\n{len(margins)} variations: agent ahead in {sum(m > 0 for m in margins)}, "
          f"margin range {min(margins):+} to {max(margins):+} "
          f"(baseline {base_agent['safety'] - base_fifo['safety']:+})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
