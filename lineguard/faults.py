"""Sensor-fault injection for robustness testing.

Real plants lose sensors to loose wires, drift and noise. SKAB has no labelled
sensor faults, so we inject them into healthy segments and keep the ground
truth. Every injected example is marked "injected" wherever it is shown.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FAULT_TYPES = ("stuck", "dropout", "spikes", "drift", "bias", "noise")
FAULT_LABELS = {
    "stuck": "Sensor stuck (flatline)",
    "dropout": "Signal lost (reads zero)",
    "spikes": "Intermittent spikes (loose contact)",
    "drift": "Calibration drift",
    "bias": "Sudden offset",
    "noise": "Excess noise (shielding)",
    "erratic": "Erratic signal (loose contact or interference)",
}


@dataclass
class InjectedFault:
    sensor: str
    kind: str
    start: int
    end: int


def inject(signals: pd.DataFrame, fault: InjectedFault, baseline_std: float, rng: np.random.Generator) -> pd.DataFrame:
    out = signals.copy()
    col = out.columns.get_loc(fault.sensor)
    values = out.iloc[:, col].to_numpy(dtype=float).copy()
    s, e = fault.start, fault.end
    n = e - s
    scale = max(baseline_std, 1e-6)
    if fault.kind == "stuck":
        values[s:e] = values[s - 1]
    elif fault.kind == "dropout":
        values[s:e] = 0.0
    elif fault.kind == "spikes":
        hits = rng.random(n) < 0.15
        values[s:e] += hits * rng.choice([-1, 1], n) * rng.uniform(6, 12, n) * scale
    elif fault.kind == "drift":
        values[s:e] += np.linspace(0, rng.uniform(4, 8), n) * scale * rng.choice([-1, 1])
    elif fault.kind == "bias":
        values[s:e] += rng.uniform(4, 7) * scale * rng.choice([-1, 1])
    elif fault.kind == "noise":
        values[s:e] += rng.normal(0, rng.uniform(3, 5) * scale, n)
    out.iloc[:, col] = values
    return out
