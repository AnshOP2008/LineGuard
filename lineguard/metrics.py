"""Evaluation metrics.

``pointwise`` reproduces SKAB's binary metric exactly
(F1 = TP / (TP + (FP + FN) / 2), FAR = FP / (FP + TN), MAR = FN / (FN + TP)),
aggregated over all runs, so our numbers are comparable with the SKAB
leaderboard. ``events`` adds the operator's view: was each fault caught, how
fast, and how many separate false alarms were raised.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .config import SHIFT_SECONDS


def pointwise(truths: Sequence[np.ndarray], preds: Sequence[np.ndarray]) -> dict[str, float]:
    tp = fp = fn = tn = 0
    for y, p in zip(truths, preds):
        y = np.asarray(y, bool)
        p = np.asarray(p, bool)
        tp += int((y & p).sum())
        fp += int((~y & p).sum())
        fn += int((y & ~p).sum())
        tn += int((~y & ~p).sum())
    return {
        "f1": tp / (tp + (fn + fp) / 2) if tp else 0.0,
        "far": 100 * fp / (fp + tn) if fp + tn else 0.0,
        "mar": 100 * fn / (fn + tp) if fn + tp else 0.0,
    }


def segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True runs."""
    edges = np.diff(np.r_[0, np.asarray(mask, int), 0])
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def events(truths: Sequence[np.ndarray], preds: Sequence[np.ndarray]) -> dict[str, float]:
    """Event-level view. A fault counts as detected when an alarm starts inside
    its window (or is already active at its start); delay is measured from the
    labelled fault start. An alarm that never touches a fault is a false alarm."""
    detected, total, false_alarms, normal_seconds = 0, 0, 0, 0
    delays: list[float] = []
    for y, p in zip(truths, preds):
        y = np.asarray(y, bool)
        p = np.asarray(p, bool)
        normal_seconds += int((~y).sum())
        for start, end in segments(y):
            total += 1
            hit = np.flatnonzero(p[start:end])
            if len(hit):
                detected += 1
                delays.append(float(hit[0]))
        for start, end in segments(p):
            if not y[start:end].any():
                false_alarms += 1
    return {
        "faults": total,
        "detected": detected,
        "recall": detected / total if total else 0.0,
        "median_delay_s": float(np.median(delays)) if delays else float("nan"),
        "mean_delay_s": float(np.mean(delays)) if delays else float("nan"),
        "false_alarm_events": false_alarms,
        "false_alarms_per_shift": false_alarms * SHIFT_SECONDS / normal_seconds if normal_seconds else 0.0,
    }
