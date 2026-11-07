"""The LineGuard detector.

For each machine run the first ``BASELINE_ROWS`` seconds are the normal
baseline (SKAB protocol; in a plant this is the commissioning period after
maintenance). Each physics-aware feature is standardised against the baseline
and divided by its own calibrated limit, so a value above 1 means "outside
anything seen while healthy, with a safety factor".

Alarm logic (all causal, so it runs online unchanged):
  * raise when the worst normalised feature exceeds 1 for ``min_duration``
    consecutive seconds (confirmation removes transients);
  * hold while it stays above ``release`` (hysteresis), clear below it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import BASELINE_ROWS, DROPOUT_SECONDS, SIGNALS, STUCK_LIMITS, DetectorConfig
from ..features import feature_frame, feature_signal


def quality_faults(signals: pd.DataFrame) -> pd.DataFrame:
    """Per-second data-quality flags: 'stuck' or 'dropout' per signal (causal)."""
    flags = {}
    for col in SIGNALS:
        x = signals[col].to_numpy(dtype=float)
        same = np.r_[False, np.diff(x) == 0]
        zero = x == 0
        run_same = _run_length(same)
        run_zero = _run_length(zero)
        flags[col] = np.where(run_zero >= DROPOUT_SECONDS, "dropout",
                              np.where(run_same >= STUCK_LIMITS[col], "stuck", ""))
    return pd.DataFrame(flags, index=signals.index)


def _run_length(mask: np.ndarray) -> np.ndarray:
    out = np.zeros(len(mask), dtype=int)
    count = 0
    for i, m in enumerate(mask):
        count = count + 1 if m else 0
        out[i] = count
    return out


@dataclass
class RunScores:
    score: np.ndarray            # worst normalised feature per second (test segment)
    alarm: np.ndarray            # confirmed alarm state per second
    normalised: pd.DataFrame     # every normalised feature (for evidence)
    feature_names: list[str]
    quality: pd.DataFrame        # data-quality flags per signal ('' when fine)


class LineGuardDetector:
    def __init__(self, config: DetectorConfig | None = None):
        self.config = config or DetectorConfig()

    # ------------------------------------------------------------------ fitting
    def fit(self, baseline: pd.DataFrame) -> "LineGuardDetector":
        cfg = self.config
        features = feature_frame(baseline, cfg.window, cfg.slope_lag)
        warm = max(cfg.window, cfg.slope_lag)
        values = features.iloc[warm:].to_numpy() if len(features) > warm + 50 else features.to_numpy()
        self.center_ = np.median(values, axis=0)
        self.scale_ = values.std(axis=0) + 1e-9
        z = np.abs((values - self.center_) / self.scale_)
        self.limit_ = np.quantile(z, cfg.baseline_quantile, axis=0) * cfg.limit_factor + 1e-9
        self.feature_names_ = list(features.columns)
        return self

    # ------------------------------------------------------------------ scoring
    def normalise(self, signals: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        features = feature_frame(signals, cfg.window, cfg.slope_lag)
        z = np.abs((features.to_numpy() - self.center_) / self.scale_) / self.limit_
        return pd.DataFrame(z, columns=self.feature_names_, index=signals.index)

    def alarm_from_score(self, score: np.ndarray) -> np.ndarray:
        cfg = self.config
        alarm = np.zeros(len(score), dtype=bool)
        active = False
        streak = 0
        for i, value in enumerate(score):
            if active:
                active = value > cfg.release
                streak = 0
            else:
                streak = streak + 1 if value > 1.0 else 0
                active = streak >= cfg.min_duration
            alarm[i] = active
        return alarm

    def score_run(self, signals: pd.DataFrame, start: int = BASELINE_ROWS) -> RunScores:
        """Score the whole run (features need history) and return the test segment."""
        normalised = self.normalise(signals)
        score = normalised.to_numpy().max(axis=1)
        alarm = self.alarm_from_score(score[start:])
        quality = quality_faults(signals).iloc[start:]
        alarm = alarm | (quality != "").any(axis=1).to_numpy()
        return RunScores(score[start:], alarm, normalised.iloc[start:], self.feature_names_, quality)


def signal_contributions(normalised_row: pd.Series) -> pd.Series:
    """Worst normalised feature per physical signal, sorted descending."""
    per_signal = normalised_row.groupby(feature_signal).max()
    return per_signal.sort_values(ascending=False)


def fit_and_score(run_signals: pd.DataFrame, config: DetectorConfig) -> RunScores:
    detector = LineGuardDetector(config).fit(run_signals.iloc[:BASELINE_ROWS])
    return detector.score_run(run_signals)
