"""Learns what you like from what you do (saved / applied vs. "not interested") — a tiny local model, no API.

Until you have at least MIN_EACH examples of both kinds it stays off and the ranking ignores it.
"""
import math
import threading

import numpy as np

MIN_EACH = 5
POSITIVE = {"saved", "opened", "applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"}
NEGATIVE = {"dismissed"}
CATS = ["ai_ml", "data_science", "data_eng", "swe", "research", "quant", "hardware", "product", "design", "other"]
MODES = ["remote", "hybrid", "onsite"]
SOURCES = ["simplify", "greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable"]
NAMES = (["semantic match", "skills match", "title match", "freshness", "CPT-friendliness", "H-1B history", "has description"]
         + [f"category: {c}" for c in CATS] + [f"mode: {m}" for m in MODES] + [f"source: {s}" for s in SOURCES])

_lock = threading.Lock()
_model: dict = {"key": None, "clf": None, "info": {"active": False}}


def features(row: dict) -> list[float]:
    p = row.get("relevance_parts") or {}
    return ([p.get("semantic", 0), p.get("skills", 0), p.get("title", 0), row["_fresh"], row["_friendly"],
             math.log1p(row.get("h1b_approvals") or 0) / 10, float(row["has_description"])]
            + [float(c in row["categories"]) for c in CATS] + [float(row["work_mode"] == m) for m in MODES]
            + [float(row["source"] == s) for s in SOURCES])


def predict(rows: list[dict], statuses: dict[str, str]) -> tuple[np.ndarray | None, dict]:
    """Probability (0-1) that you'd want each row, or None while there isn't enough feedback yet."""
    labeled = [(r, 1 if statuses[r["id"]] in POSITIVE else 0) for r in rows
               if statuses.get(r["id"]) in POSITIVE | NEGATIVE]
    pos = sum(y for _, y in labeled)
    neg = len(labeled) - pos
    if pos < MIN_EACH or neg < MIN_EACH:
        return None, {"active": False, "positive": pos, "negative": neg, "needed": MIN_EACH}
    key = tuple(sorted((r["id"], y) for r, y in labeled))
    with _lock:
        if _model["key"] != key:
            from sklearn.linear_model import LogisticRegression
            X = np.array([features(r) for r, _ in labeled])
            y = np.array([y for _, y in labeled])
            clf = LogisticRegression(class_weight="balanced", C=0.5, max_iter=500).fit(X, y)
            top = sorted(zip(NAMES, clf.coef_[0]), key=lambda t: -abs(t[1]))[:6]
            _model.update(key=key, clf=clf, info={"active": True, "positive": pos, "negative": neg,
                                                  "likes": [n for n, w in top if w > 0], "dislikes": [n for n, w in top if w < 0]})
        clf, info = _model["clf"], _model["info"]
    return clf.predict_proba(np.array([features(r) for r in rows]))[:, 1], info
