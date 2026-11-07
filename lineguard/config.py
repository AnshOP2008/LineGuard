"""Central configuration: paths, signal names and the shipped detector settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("LINEGUARD_DATA", ROOT / "data"))
SKAB_DIR = DATA_DIR / "skab"
HYDRAULIC_DIR = DATA_DIR / "hydraulic"
RESULTS_DIR = ROOT / "results"
ARTIFACTS_DIR = ROOT / "artifacts"
WEB_DATA_DIR = ROOT / "web" / "data"

# SKAB signal columns (8 sensors on the water-circulation pump rig).
SIGNALS: list[str] = [
    "Accelerometer1RMS",
    "Accelerometer2RMS",
    "Current",
    "Pressure",
    "Temperature",
    "Thermocouple",
    "Voltage",
    "Volume Flow RateRMS",
]

# Human-friendly names and units shown in the dashboard.
SIGNAL_INFO: dict[str, dict[str, str]] = {
    "Accelerometer1RMS": {"label": "Vibration A", "unit": "g", "group": "vibration"},
    "Accelerometer2RMS": {"label": "Vibration B", "unit": "g", "group": "vibration"},
    "Current": {"label": "Motor current", "unit": "A", "group": "electrical"},
    "Pressure": {"label": "Loop pressure", "unit": "bar", "group": "hydraulic"},
    "Temperature": {"label": "Motor temperature", "unit": "°C", "group": "thermal"},
    "Thermocouple": {"label": "Fluid temperature", "unit": "°C", "group": "thermal"},
    "Voltage": {"label": "Motor voltage", "unit": "V", "group": "electrical"},
    "Volume Flow RateRMS": {"label": "Flow rate", "unit": "L/min", "group": "hydraulic"},
}

# Thermal signals drift slowly (warm-up, and they settle at a new level after a
# fault clears). Their absolute level is not evidence of a fault, but an
# unusual *rate of change* is. Every other signal is judged by its level.
SLOW_SIGNALS: list[str] = ["Temperature", "Thermocouple"]
FAST_SIGNALS: list[str] = [s for s in SIGNALS if s not in SLOW_SIGNALS]

# SKAB's official protocol: the first 400 rows of every run are the
# (normal-only) training baseline; everything after is the test segment.
BASELINE_ROWS = 400

SAMPLING_SECONDS = 1.0
SHIFT_SECONDS = 8 * 3600


@dataclass(frozen=True)
class DetectorConfig:
    """Hyper-parameters of the LineGuard detector.

    window      rolling window (s) for level / variability features
    slope_lag   lag (s) for the rate-of-change feature of thermal signals
    limit_factor  alarm limit = limit_factor x 99.9th pct of |z| on the baseline
    release     hysteresis: an active alarm clears when the score drops below this
    min_duration  alarms shorter than this (s) are discarded as transients
    """

    window: int = 20
    slope_lag: int = 60
    limit_factor: float = 2.0
    release: float = 0.8
    min_duration: int = 30
    baseline_quantile: float = 0.999
    name: str = "balanced"


@dataclass(frozen=True)
class SearchSpace:
    """Grid explored by nested cross-validation (see lineguard.tuning)."""

    window: list[int] = field(default_factory=lambda: [5, 10, 20])
    slope_lag: list[int] = field(default_factory=lambda: [30, 60])
    limit_factor: list[float] = field(default_factory=lambda: [1.6, 2.0, 2.5, 3.0])
    release: list[float] = field(default_factory=lambda: [1.0, 0.8, 0.6])
    min_duration: list[int] = field(default_factory=lambda: [1, 10, 30, 60])


# Data-quality limits: a reading repeated identically for this many seconds is
# treated as a stuck sensor. Derived as 2x the longest identical run observed in
# any healthy SKAB data (pressure 18 s, flow 42 s; quantised sensors), with a
# 20 s floor. tests/test_quality.py checks no healthy data trips them.
STUCK_LIMITS: dict[str, int] = {s: 20 for s in SIGNALS} | {"Pressure": 36, "Volume Flow RateRMS": 84}
# Exact zeros never occur on this rig; 3 consecutive zeros = signal lost.
DROPOUT_SECONDS = 3

# Operating modes. "low_false_alarm" restricts tuning to settings whose
# false-alarm rate on the tuning folds is at most this many percent.
LOW_FALSE_ALARM_CAP = 15.0
