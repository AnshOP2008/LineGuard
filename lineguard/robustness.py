"""Sensor-fault robustness study (deck scenario 3).

1. Inject sensor faults (stuck, dropout, spikes, drift, bias, noise) into
   healthy time: the pre-fault part of every SKAB run plus chunks of SKAB's
   anomaly-free recording. Real faults are never overlapped.
2. Measure how many injected faults LineGuard alarms on.
3. For every alarm on a real (machine) fault and every alarm on an injected
   (sensor) fault, record the verdict of ``isolation.classify``.

Relationship models are trained without the experiment family under test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BASELINE_ROWS, SIGNALS, DetectorConfig
from .data import Run
from .detectors.lineguard import LineGuardDetector
from .faults import FAULT_TYPES, InjectedFault, inject
from .isolation import alarm_events, alarm_evidence, classify
from .relationships import RelationshipModel, normal_training_frames, relative_levels

FAMILIES = ("valve1", "valve2", "other", "healthy")


def healthy_chunks(anomaly_free: pd.DataFrame, size: int = 1200) -> list[Run]:
    out = []
    for i, start in enumerate(range(0, len(anomaly_free) - size, size)):
        chunk = anomaly_free.iloc[start:start + size].reset_index(drop=True).copy()
        chunk["anomaly"] = 0
        chunk["changepoint"] = 0
        out.append(Run(f"healthy/{i}", "healthy", chunk))
    return out


def relationship_models(runs: list[Run], anomaly_free: pd.DataFrame) -> dict[str, RelationshipModel]:
    models = {}
    for family in FAMILIES:
        frames = normal_training_frames(runs, None if family == "healthy" else anomaly_free,
                                        exclude_group=family)
        models[family] = RelationshipModel().fit(frames)
    return models


def injected_cases(runs: list[Run], chunks: list[Run], seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    cases = []
    for run in runs + chunks:
        labels = run.anomaly
        healthy_end = int(np.flatnonzero(labels)[0]) if labels.any() else len(labels)
        if healthy_end - BASELINE_ROWS < 80:
            continue
        signals = run.signals.iloc[:healthy_end].reset_index(drop=True)
        base_std = signals.iloc[:BASELINE_ROWS].std()
        for kind in FAULT_TYPES:
            for sensor in rng.choice(SIGNALS, size=2, replace=False):
                start = int(rng.integers(BASELINE_ROWS + 20, healthy_end - 50))
                end = int(min(healthy_end, start + rng.integers(50, 200)))
                fault = InjectedFault(str(sensor), kind, start, end)
                cases.append({"run": run, "fault": fault,
                              "signals": inject(signals, fault, float(base_std[sensor]), rng)})
    return cases


def _evidence(signals, det, scores, relation, start, end):
    quality = scores.quality.set_axis(range(BASELINE_ROWS, len(signals)))
    ev = alarm_evidence(signals, det.normalise(signals), relative_levels(signals), relation, start, end, quality)
    return ev, classify(ev)


def study(runs: list[Run], anomaly_free: pd.DataFrame, config: DetectorConfig, seed: int = 0) -> dict:
    relations = relationship_models(runs, anomaly_free)
    rows = []
    for run in runs:
        det = LineGuardDetector(config).fit(run.signals.iloc[:BASELINE_ROWS])
        scores = det.score_run(run.signals)
        for start, end in alarm_events(scores.alarm):
            if run.anomaly[start:end].any():
                ev, verdict = _evidence(run.signals, det, scores, relations[run.group], start, end)
                rows.append({"truth": "machine", "kind": "machine", "run": run.name, "verdict": verdict.kind,
                             "lead": ev.lead_signal, "true_sensor": None, "named": verdict.fault_type})
                break

    detection = {k: {"detected": 0, "injected": 0} for k in FAULT_TYPES}
    for case in injected_cases(runs, healthy_chunks(anomaly_free), seed):
        run, fault, signals = case["run"], case["fault"], case["signals"]
        det = LineGuardDetector(config).fit(signals.iloc[:BASELINE_ROWS])
        scores = det.score_run(signals)
        detection[fault.kind]["injected"] += 1
        hits = [(s, e) for s, e in alarm_events(scores.alarm) if s < fault.end and e > fault.start]
        if not hits:
            continue
        detection[fault.kind]["detected"] += 1
        ev, verdict = _evidence(signals, det, scores, relations[run.group], *hits[0])
        rows.append({"truth": "sensor", "kind": fault.kind, "run": run.name, "verdict": verdict.kind,
                     "lead": ev.lead_signal, "true_sensor": fault.sensor, "named": verdict.fault_type})

    table = pd.DataFrame(rows)
    crosstab = pd.crosstab(table.kind, table.verdict).reindex(
        index=["machine", *FAULT_TYPES], columns=["machine", "sensor", "unconfirmed"], fill_value=0)
    sensors = table[table.truth == "sensor"]
    sensor_calls = sensors[sensors.verdict == "sensor"]
    machine = table[table.truth == "machine"]
    signature = sensors[sensors.kind.isin(["stuck", "dropout", "spikes", "noise"])]
    named = sensor_calls.named == sensor_calls.kind.replace({"spikes": "erratic", "noise": "erratic"})
    return {
        "detection": detection,
        "verdicts": {k: {c: int(v) for c, v in row.items()} for k, row in crosstab.iterrows()},
        "machine_dismissed_as_sensor": float((machine.verdict == "sensor").mean()),
        "machine_confirmed": float((machine.verdict == "machine").mean()),
        "signature_faults_called_sensor": float((signature.verdict == "sensor").mean()),
        "sensor_faults_called_machine": float((sensors.verdict == "machine").mean()),
        "correct_sensor_named": float((sensor_calls.lead == sensor_calls.true_sensor).mean()),
        "failure_mode_named": float(named.mean()),
        "n_machine": int(len(machine)),
        "n_sensor_alarms": int(len(sensors)),
        "seed": seed,
    }
