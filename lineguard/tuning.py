"""Hyper-parameter selection with nested leave-one-family-out cross-validation.

SKAB has three experiment families (valve1, valve2, other). To get an honest
estimate, settings are chosen on two families and scored on the third, three
times. The reported number never sees its own test family during tuning.
The shipped configuration is then chosen on all runs, which is standard
practice: the nested score estimates how well that procedure generalises.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, replace

import numpy as np

from .config import BASELINE_ROWS, LOW_FALSE_ALARM_CAP, DetectorConfig, SearchSpace
from .data import Run
from .detectors.lineguard import LineGuardDetector
from .metrics import pointwise

FAMILIES = ("valve1", "valve2", "other")


def _candidate_configs(space: SearchSpace) -> list[DetectorConfig]:
    return [
        DetectorConfig(window=w, slope_lag=s, limit_factor=f, release=r, min_duration=m)
        for w, s, f, r, m in itertools.product(
            space.window, space.slope_lag, space.limit_factor, space.release, space.min_duration)
    ]


def _all_predictions(runs: list[Run], configs: list[DetectorConfig]) -> dict[DetectorConfig, list[np.ndarray]]:
    """Alarms for every config. Scores depend only on (window, lag, factor),
    so they are computed once and the cheap alarm logic is re-run per config."""
    cache: dict[tuple, list[np.ndarray]] = {}
    out: dict[DetectorConfig, list[np.ndarray]] = {}
    for cfg in configs:
        key = (cfg.window, cfg.slope_lag, cfg.limit_factor)
        if key not in cache:
            scores = []
            for run in runs:
                det = LineGuardDetector(cfg).fit(run.signals.iloc[:BASELINE_ROWS])
                scores.append(det.normalise(run.signals).to_numpy().max(axis=1)[BASELINE_ROWS:])
            cache[key] = scores
        det = LineGuardDetector(cfg)
        out[cfg] = [det.alarm_from_score(s) for s in cache[key]]
    return out


def select(runs, preds, truths, idx, cap):
    def metric(cfg):
        return pointwise([truths[i] for i in idx], [preds[cfg][i] for i in idx])
    candidates = list(preds)
    if cap is not None:
        allowed = [c for c in candidates if metric(c)["far"] <= cap]
        candidates = allowed or candidates
    return max(candidates, key=lambda c: metric(c)["f1"])


def nested_cv(runs: list[Run], space: SearchSpace | None = None) -> dict:
    configs = _candidate_configs(space or SearchSpace())
    preds = _all_predictions(runs, configs)
    truths = [r.anomaly[BASELINE_ROWS:] for r in runs]
    groups = np.array([r.group for r in runs])
    report = {}
    for mode, cap in (("balanced", None), ("low_false_alarm", LOW_FALSE_ALARM_CAP)):
        nested_preds: list[np.ndarray | None] = [None] * len(runs)
        chosen = {}
        for family in FAMILIES:
            train = np.flatnonzero(groups != family)
            test = np.flatnonzero(groups == family)
            best = select(runs, preds, truths, train, cap)
            chosen[family] = asdict(best)
            for i in test:
                nested_preds[i] = preds[best][i]
        final = replace(select(runs, preds, truths, range(len(runs)), cap), name=mode)
        report[mode] = {
            "nested_pointwise": pointwise(truths, nested_preds),
            "nested_predictions": nested_preds,
            "chosen_per_fold": chosen,
            "shipped_config": final,
            "shipped_in_sample": pointwise(truths, preds[replace(final, name="balanced")]),
        }
    report["n_configs"] = len(configs)
    return report
