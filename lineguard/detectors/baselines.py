"""Reference detectors, each trained on the same 400-row normal baseline per run.

These form the bottom rungs of the model ladder: LineGuard must beat them on
identical splits to earn its place.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


def _smooth(mask: np.ndarray, window: int = 5) -> np.ndarray:
    return pd.Series(mask.astype(float)).rolling(window, min_periods=1).mean().round().astype(bool).to_numpy()


def rolling_zscore(train: pd.DataFrame, test: pd.DataFrame, quantile: float = 0.999) -> np.ndarray:
    """Rung 1: per-sensor limits, one sensor at a time (classic threshold alarm)."""
    scaler = StandardScaler().fit(train)
    z_train = np.abs(scaler.transform(train)).max(axis=1)
    z_test = np.abs(scaler.transform(test)).max(axis=1)
    return _smooth(z_test > np.quantile(z_train, quantile))


def pca_t2_q(train: pd.DataFrame, test: pd.DataFrame, quantile: float = 0.99,
             variance: float = 0.85) -> np.ndarray:
    """Rung 2: Hotelling T² + Q (SPE) statistical process control, as in SKAB."""
    scaler = StandardScaler().fit(train)
    a, b = scaler.transform(train), scaler.transform(test)
    pca = PCA(n_components=variance).fit(a)

    def stats(x):
        t = pca.transform(x)
        t2 = (t ** 2 / pca.explained_variance_).sum(axis=1)
        q = ((x - pca.inverse_transform(t)) ** 2).sum(axis=1)
        return t2, q

    t2_a, q_a = stats(a)
    t2_b, q_b = stats(b)
    alarm = (t2_b > np.quantile(t2_a, quantile)) | (q_b > np.quantile(q_a, quantile))
    return _smooth(alarm)


def isolation_forest(train: pd.DataFrame, test: pd.DataFrame, quantile: float = 0.99,
                     seed: int = 0) -> np.ndarray:
    """Rung 3: tree-based outlier detection."""
    scaler = StandardScaler().fit(train)
    model = IsolationForest(n_estimators=200, random_state=seed).fit(scaler.transform(train))
    s_train = -model.score_samples(scaler.transform(train))
    s_test = -model.score_samples(scaler.transform(test))
    return _smooth(s_test > np.quantile(s_train, quantile))
