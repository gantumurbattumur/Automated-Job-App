from datetime import datetime

FRIENDLINESS = {"friendly": 1.0, "unclear": 0.4, "blocked": 0.0}


def freshness(posted_at: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max((now - posted_at).total_seconds() / 86400, 0)
    return 0.5 ** (age_days / half_life_days)


def score(rel: float, fresh: float, friendly: float, weights: dict, boost: float = 0,
          personal: float | None = None) -> tuple[float, dict]:
    """0-100 (+boost) and each component's contribution in points. `personal` is skipped until the model is trained."""
    parts = {"relevance": rel, "freshness": fresh, "friendliness": friendly}
    if personal is not None:
        parts["personal"] = personal
    w = {k: weights.get(k, 0) for k in parts}
    total = sum(w.values()) or 1  # sliders needn't sum to 1
    points = {k: round(100 * w[k] * v / total, 1) for k, v in parts.items()}
    if boost:
        points["allowlist"] = boost
    return round(sum(points.values()), 1), points
