"""Tests on the real datasets (skipped when they are not downloaded)."""

import json

import numpy as np
import pytest

from lineguard.config import BASELINE_ROWS, RESULTS_DIR, SIGNALS, SKAB_DIR, STUCK_LIMITS, DetectorConfig

pytestmark = pytest.mark.skipif(not any(SKAB_DIR.glob("**/valve1")), reason="SKAB not downloaded")


@pytest.fixture(scope="module")
def runs():
    from lineguard.data import load_runs
    return load_runs()


def test_skab_shape(runs):
    assert len(runs) == 34
    assert {r.group for r in runs} == {"valve1", "valve2", "other"}
    assert all(list(r.signals.columns) == SIGNALS for r in runs)


def test_stuck_limits_never_trip_on_healthy_data(runs):
    from lineguard.data import load_anomaly_free
    from lineguard.detectors.lineguard import quality_faults

    for frame in [r.signals for r in runs] + [load_anomaly_free()[SIGNALS]]:
        assert (quality_faults(frame) == "").all().all()
    assert STUCK_LIMITS["Volume Flow RateRMS"] >= 2 * 42


def test_online_matches_batch(runs):
    from lineguard.detectors.lineguard import fit_and_score
    from lineguard.online import OnlineMonitor
    from lineguard.relationships import RelationshipModel, normal_training_frames

    cfg = DetectorConfig()
    run = next(r for r in runs if r.name == "valve1/3")
    batch = fit_and_score(run.signals, cfg).alarm
    mon = OnlineMonitor(cfg, RelationshipModel(n_estimators=20).fit(normal_training_frames(runs[:5], None)))
    online = [mon.push(row.to_dict()) for _, row in run.signals.iterrows()]
    states = np.array([e["state"] == "alarm" for e in online[BASELINE_ROWS:]])
    assert (states == batch).mean() > 0.995


def test_published_results_are_consistent():
    path = RESULTS_DIR / "results.json"
    if not path.exists():
        pytest.skip("run `python -m lineguard evaluate` first")
    r = json.loads(path.read_text())
    ours = r["ladder"]["LineGuard, balanced (nested CV)"]
    assert 0 < ours["f1"] <= 1 and ours["faults"] == 34
