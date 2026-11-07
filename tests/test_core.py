"""Unit tests that need no dataset (run in CI on every push)."""

import numpy as np
import pandas as pd
import pytest

from lineguard.config import BASELINE_ROWS, SIGNALS, DetectorConfig
from lineguard.detectors.lineguard import LineGuardDetector, quality_faults
from lineguard.faults import InjectedFault, inject
from lineguard.metrics import events, pointwise, segments
from lineguard.simulation import LineAssumptions, compare


def synthetic_run(n=1200, seed=0, fault=(800, 1000)):
    rng = np.random.default_rng(seed)
    base = {"Accelerometer1RMS": 0.2, "Accelerometer2RMS": 0.27, "Current": 2.4, "Pressure": 0.1,
            "Temperature": 89.0, "Thermocouple": 28.0, "Voltage": 229.0, "Volume Flow RateRMS": 125.0}
    noise = {"Accelerometer1RMS": 0.003, "Accelerometer2RMS": 0.003, "Current": 0.4, "Pressure": 0.25,
             "Temperature": 0.3, "Thermocouple": 0.05, "Voltage": 10.0, "Volume Flow RateRMS": 0.5}
    frame = pd.DataFrame({s: base[s] + rng.normal(0, noise[s], n) for s in SIGNALS})
    frame["Temperature"] += np.linspace(0, 0.6, n)  # slow warm-up drift
    if fault:
        a, b = fault
        frame.loc[a:b, "Volume Flow RateRMS"] -= 4.0   # flow loss
        frame.loc[a:b, "Pressure"] += 0.5
    return frame


def test_pointwise_matches_skab_formula():
    y = [np.array([0, 0, 1, 1, 1, 0], bool)]
    p = [np.array([0, 1, 1, 1, 0, 0], bool)]
    m = pointwise(y, p)  # TP=2 FP=1 FN=1 TN=2
    assert m["f1"] == pytest.approx(2 / (2 + 1))
    assert m["far"] == pytest.approx(100 / 3)
    assert m["mar"] == pytest.approx(100 / 3)


def test_events_counts_delay_and_false_alarms():
    y = np.zeros(100, bool); y[40:60] = True
    p = np.zeros(100, bool); p[45:70] = True; p[80:85] = True
    e = events([y], [p])
    assert e["detected"] == 1 and e["median_delay_s"] == 5 and e["false_alarm_events"] == 1
    assert segments(p) == [(45, 70), (80, 85)]


def test_detector_finds_fault_and_stays_quiet_on_healthy():
    det = LineGuardDetector(DetectorConfig()).fit(synthetic_run().iloc[:BASELINE_ROWS])
    scores = det.score_run(synthetic_run())
    alarm = np.r_[np.zeros(BASELINE_ROWS, bool), scores.alarm]
    assert alarm[810:1000].mean() > 0.8
    healthy = synthetic_run(seed=1, fault=None)
    det2 = LineGuardDetector(DetectorConfig()).fit(healthy.iloc[:BASELINE_ROWS])
    assert det2.score_run(healthy).alarm.mean() < 0.02


def test_scores_are_causal():
    """Changing the future must not change any past score."""
    run = synthetic_run()
    det = LineGuardDetector(DetectorConfig()).fit(run.iloc[:BASELINE_ROWS])
    full = det.score_run(run).score
    altered = run.copy()
    altered.iloc[900:] += 50.0
    cut = det.score_run(altered).score
    k = 900 - BASELINE_ROWS
    np.testing.assert_allclose(full[:k], cut[:k])


def test_quality_check_flags_stuck_and_dropout():
    run = synthetic_run(fault=None)
    rng = np.random.default_rng(0)
    stuck = inject(run, InjectedFault("Current", "stuck", 600, 700), 0.4, rng)
    q = quality_faults(stuck)
    assert (q["Current"].iloc[620:700] == "stuck").all() and (q.drop(columns="Current") == "").all().all()
    dead = inject(run, InjectedFault("Pressure", "dropout", 600, 700), 0.25, rng)
    assert (quality_faults(dead)["Pressure"].iloc[605:700] == "dropout").all()


def test_simulation_inspecting_beats_running_to_failure():
    out = compare(LineAssumptions(), runs=30)
    assert out["inspect_now"]["good_units_mean"] > out["run_to_failure"]["good_units_mean"]
    assert out["label"].startswith("SIMULATED")
