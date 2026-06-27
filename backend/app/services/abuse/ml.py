"""
Isolation Forest anomaly scorer — SCAFFOLD (wired, not yet trained).

[LEARN]
Pattern: "Isolation Forest" (unsupervised anomaly detection).
The idea: build many random binary trees that split features at random
thresholds. Normal points sit in dense regions and need many splits to be
isolated; anomalies sit alone and get isolated in very few splits. The mean
path length to isolate a point → an anomaly score in [0, 1] (higher = more
anomalous). Great for "I don't have labelled fraud, just normal-ish traffic".

Why a stub right now (and NOT a fake model):
  A meaningful model needs real traffic to learn "normal" from — request
  volume, inter-request frequency, hour-of-day, channel mix per recipient/IP.
  We have the call site wired and the feature shape defined, but training on
  invented data would produce confident-but-meaningless blocks. So we return a
  neutral score; the rule engine carries the load until we can fit on real
  data (Month-2 stretch / Month-4 hardening).

Feature vector we WILL train on (documented contract):
  - reqs_last_hour      (per recipient)
  - reqs_last_day       (per recipient)
  - reqs_last_hour_ip   (per IP)
  - hour_of_day         (0-23)
  - distinct_recipients_per_ip_last_hour

Read more:
  - Liu, Ting, Zhou (2008), "Isolation Forest"
  - sklearn.ensemble.IsolationForest — decision_function / score_samples
"""

from __future__ import annotations

# Neutral score = "no ML signal". The engine treats this as not-anomalous.
NEUTRAL_SCORE = 0.0


def anomaly_score(features: dict[str, float]) -> float:
    """
    Return an anomaly score in [0, 1] (higher = more anomalous).

    SCAFFOLD: always returns NEUTRAL_SCORE until a trained model is loaded.
    Replace the body with `model.score_samples(...)` normalised to [0, 1]
    once `IsolationForest` is fitted on real traffic. Signature is stable so
    the engine call site never changes.
    """
    return NEUTRAL_SCORE