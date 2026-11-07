"""Physics-aware, strictly causal features computed from the raw 1 Hz signals.

* Level (rolling mean) for signals whose absolute value is meaningful:
  vibration, current, voltage, pressure and flow.
* Rate of change for the two thermal signals. Temperatures drift during warm-up
  and settle at a new level after a fault clears, so their level is not
  evidence, but an unusual heating or cooling *rate* is.
* Variability (rolling standard deviation) for every signal. This catches
  turbulence and cavitation, and also a stuck sensor (variability collapses).

Every feature at time t uses samples <= t only, so the same code runs online.
"""

from __future__ import annotations

import pandas as pd

from .config import FAST_SIGNALS, SIGNALS, SLOW_SIGNALS


def feature_frame(signals: pd.DataFrame, window: int, slope_lag: int) -> pd.DataFrame:
    rolling = signals.rolling(window, min_periods=1)
    mean = rolling.mean()
    level = mean[FAST_SIGNALS].add_suffix("::level")
    variability = rolling.std().fillna(0.0)[SIGNALS].add_suffix("::variability")
    slope = (mean[SLOW_SIGNALS] - mean[SLOW_SIGNALS].shift(slope_lag))
    # The first `slope_lag` rows have no history yet: fall back to the earliest
    # available difference (these rows sit inside the training baseline).
    slope = slope.bfill().add_suffix("::rate")
    return pd.concat([level, variability, slope], axis=1)


def feature_signal(feature_name: str) -> str:
    return feature_name.split("::", 1)[0]


def feature_kind(feature_name: str) -> str:
    return feature_name.split("::", 1)[1]
