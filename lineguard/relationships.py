"""Cross-signal relationship model: predict every sensor from the others.

Each run is first expressed relative to its own healthy baseline (z-scores),
so one model learns relationships that hold across operating points, such as
"when current and pressure are normal, flow should be normal". The model is
trained on normal data only (SKAB's anomaly-free recording and the baseline
segment of every run).

At alarm time it answers the operator's real question: is this deviation
*supported by the other sensors* (a physical change in the machine), or is
one sensor reporting something the others contradict (a sensor problem)?
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from .config import BASELINE_ROWS, SIGNALS

SMOOTH = 10


def relative_levels(signals: pd.DataFrame, baseline_rows: int = BASELINE_ROWS) -> pd.DataFrame:
    smooth = signals[SIGNALS].rolling(SMOOTH, min_periods=1).mean()
    base = smooth.iloc[SMOOTH:baseline_rows]
    return (smooth - base.median()) / (base.std() + 1e-9)


class RelationshipModel:
    def __init__(self, n_estimators: int = 200, seed: int = 0):
        self.n_estimators = n_estimators
        self.seed = seed
        self.models: dict[str, LGBMRegressor] = {}

    def fit(self, normal_frames: list[pd.DataFrame]) -> "RelationshipModel":
        z = pd.concat(normal_frames, ignore_index=True).clip(-8, 8)
        for target in SIGNALS:
            model = LGBMRegressor(n_estimators=self.n_estimators, learning_rate=0.05, num_leaves=15,
                                  min_child_samples=40, subsample=0.8, subsample_freq=1,
                                  colsample_bytree=0.9, random_state=self.seed, verbosity=-1)
            model.fit(z.drop(columns=target), z[target])
            self.models[target] = model
        residual = self.residuals(z)
        self.residual_scale_ = residual.abs().quantile(0.99) + 1e-6
        return self

    def predict(self, z: pd.DataFrame) -> pd.DataFrame:
        z = z.clip(-8, 8)
        return pd.DataFrame({t: m.predict(z.drop(columns=t)) for t, m in self.models.items()}, index=z.index)

    def residuals(self, z: pd.DataFrame) -> pd.DataFrame:
        return z.clip(-8, 8) - self.predict(z)

    def importance(self) -> dict[str, dict[str, float]]:
        """Normalised gain importance: which signals explain each signal."""
        out = {}
        for target, model in self.models.items():
            gain = model.booster_.feature_importance("gain")
            names = [c for c in SIGNALS if c != target]
            total = gain.sum() or 1.0
            out[target] = {n: float(g / total) for n, g in zip(names, gain)}
        return out


def normal_training_frames(runs, anomaly_free: pd.DataFrame | None, exclude_group: str | None = None):
    frames = []
    for run in runs:
        if exclude_group and run.group == exclude_group:
            continue
        z = relative_levels(run.signals)
        frames.append(z.iloc[SMOOTH:BASELINE_ROWS])
    if anomaly_free is not None:
        # The long healthy recording, split into run-sized chunks with their own baseline.
        for start in range(0, len(anomaly_free) - 1200, 1200):
            chunk = anomaly_free[SIGNALS].iloc[start:start + 1200].reset_index(drop=True)
            frames.append(relative_levels(chunk).iloc[SMOOTH:])
    return frames
