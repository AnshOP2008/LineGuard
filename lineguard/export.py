"""Export everything the static dashboard needs into web/data/.

    python -m lineguard export

Every number in the dashboard comes from these files, which are produced by
the same code that the evaluation uses.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import asdict

import numpy as np
import pandas as pd

from .config import (ARTIFACTS_DIR, BASELINE_ROWS, RESULTS_DIR, SIGNAL_INFO, SIGNALS, SLOW_SIGNALS,
                     WEB_DATA_DIR, DetectorConfig)
from .data import Run, load_anomaly_free, load_runs
from .detectors import baselines
from .detectors.lineguard import LineGuardDetector
from .faults import FAULT_LABELS, InjectedFault, inject
from .isolation import PARTNERS, alarm_events, alarm_evidence, classify
from .metrics import segments
from .relationships import relative_levels
from .robustness import healthy_chunks, relationship_models

CAUSE = {
    "hydraulic": "Restriction or leak in the pump loop, for example a partly closed or sticking valve.",
    "vibration": "Mechanical change at the pump: imbalance, looseness or cavitation.",
    "thermal": "Change in heat balance: cooling, friction or fluid temperature.",
    "electrical": "Change in motor load or supply.",
}
INSPECT = {
    "hydraulic": "valves and pipework on the pump loop",
    "vibration": "pump mounting, coupling and impeller",
    "thermal": "motor cooling and fluid circuit",
    "electrical": "motor supply and load",
}


def _label(sig: str) -> str:
    return SIGNAL_INFO[sig]["label"]


def _lower(sig: str) -> str:
    first, *rest = _label(sig).split(" ")
    return " ".join([first.lower(), *rest])


def _round(values, digits=4) -> list:
    arr = np.asarray(values, dtype=float)
    out = []
    for v in arr:
        if not np.isfinite(v):
            out.append(None)
        else:
            out.append(float(f"{v:.{digits}g}"))
    return out


def _ranges(mask, offset=0) -> list[list[int]]:
    return [[int(s + offset), int(e + offset)] for s, e in segments(np.asarray(mask, bool))]


WATCH_LEVEL = 0.8      # early-warning level on the health score (alarm limit = 1.0)
WATCH_SECONDS = 10     # must hold this long, outside an alarm


def watch_ranges(score_test: np.ndarray, alarm: np.ndarray) -> np.ndarray:
    """Early warning: score at or above WATCH_LEVEL for WATCH_SECONDS while no alarm is active (causal)."""
    above = (np.nan_to_num(score_test) >= WATCH_LEVEL) & ~alarm
    run, out = 0, np.zeros(len(above), bool)
    for i, a in enumerate(above):
        run = run + 1 if a else 0
        out[i] = run >= WATCH_SECONDS
    return out


def _alert_payload(ev, verdict, start: int, end: int, truth: str) -> dict:
    lead = ev.lead_signal
    info = SIGNAL_INFO[lead]
    z = ev.observed[lead]
    lead_kind_var = ev.variability[lead] >= 2 and abs(z) < 3
    if lead in SLOW_SIGNALS:
        direction, size = ("rising" if z > 0 else "falling") + " unusually fast", ""
    elif lead_kind_var:
        direction, size = "became erratic", ""
    else:
        direction, size = ("rose" if z > 0 else "fell"), (" more than 8σ" if abs(z) >= 7.95 else f" {abs(z):.1f}σ")
    partner = verdict.partner
    if verdict.kind == "machine":
        title = f"{info['label']} {direction}{size}, confirmed by {_lower(partner)}"
        cause = CAUSE[info["group"]]
        action = (f"Inspect the {INSPECT[info['group']]} on pump P-101 this shift. "
                  "Assembly and Packing depend on this station.")
        confidence = "High" if sum(v > 1 for v in ev.contributions.values()) >= 2 else "Medium"
    elif verdict.kind == "sensor":
        title = f"{info['label']} sensor looks faulty: {FAULT_LABELS.get(verdict.fault_type, verdict.fault_type).lower()}"
        cause = "Instrument problem, not the machine: wiring, connector, power or calibration."
        action = (f"Check the {info['label'].lower()} transmitter and its wiring before sending mechanical maintenance. "
                  "Treat its readings as unreliable until it is checked.")
        confidence = "High" if ev.quality_flags else "Medium"
    else:
        title = f"{info['label']} {direction}{size}, not yet confirmed"
        cause = CAUSE[info["group"]] + " Could also be a sensor offset."
        action = (f"Check the {info['label'].lower()} reading against a local gauge (a two-minute check). "
                  "If it agrees, treat this as a machine fault.")
        confidence = "Low"
    checks = {
        "stuck": ev.quality_flags.get(lead) == "stuck" or ev.features["lead_flat_fraction"] > 0.6,
        "dropout": ev.quality_flags.get(lead) == "dropout" or ev.features["lead_zero_fraction"] > 0.5,
        "spikes": ev.jumps[lead] >= 3,
        "noise": ev.variability[lead] >= 2,
    }
    return {
        "start": int(start), "end": int(end), "verdict": verdict.kind, "fault_type": verdict.fault_type,
        "lead": lead, "partner": partner, "partners": PARTNERS[lead],
        "title": title, "reasons": verdict.reasons, "cause": cause, "action": action, "confidence": confidence,
        "contributions": {k: round(v, 3) for k, v in ev.contributions.items()},
        "observed": {k: round(v, 2) for k, v in ev.observed.items()},
        "expected": {k: round(v, 2) for k, v in ev.predicted.items()},
        "variability": {k: round(v, 2) for k, v in ev.variability.items()},
        "jumps": {k: round(v, 2) for k, v in ev.jumps.items()},
        "checks": checks, "quality_flags": ev.quality_flags, "truth": truth,
    }


def run_payload(run: Run, config: DetectorConfig, relation, title: str, note: str, tag: str,
                injected: InjectedFault | None = None, signals: pd.DataFrame | None = None) -> dict:
    signals = run.signals if signals is None else signals
    det = LineGuardDetector(config).fit(signals.iloc[:BASELINE_ROWS])
    scores = det.score_run(signals)
    normalised_full = det.normalise(signals)
    contrib_full = normalised_full.T.groupby(lambda c: c.split("::")[0]).max().T.reindex(columns=SIGNALS)
    levels = relative_levels(signals)
    quality = scores.quality.set_axis(range(BASELINE_ROWS, len(signals)))
    labels = run.anomaly[: len(signals)]
    alerts = []
    for start, end in alarm_events(scores.alarm):
        ev = alarm_evidence(signals, normalised_full, levels, relation, start, end, quality)
        verdict = classify(ev)
        if injected and start < injected.end and end > injected.start:
            truth = f"Injected sensor fault: {FAULT_LABELS[injected.kind].lower()} on {_lower(injected.sensor)}."
        elif labels[start:end].any():
            truth = "Overlaps a fault labelled in SKAB."
        else:
            truth = "No labelled fault here: this alarm counts as a false alarm in our metrics."
        alerts.append(_alert_payload(ev, verdict, start, end, truth))
    t2q = baselines.pca_t2_q(signals.iloc[:BASELINE_ROWS], signals.iloc[BASELINE_ROWS:])
    return {
        "id": run.name, "title": title, "note": note, "tag": tag,
        "n": int(len(signals)), "baseline_end": BASELINE_ROWS,
        "start_time": str(run.frame["datetime"].iloc[0]),
        "signals": {s: _round(signals[s].to_numpy()) for s in SIGNALS},
        "contrib": {s: _round(np.clip(contrib_full[s].to_numpy(), 0, 6), 3) for s in SIGNALS},
        "score": _round(np.clip(np.r_[np.full(BASELINE_ROWS, np.nan), scores.score], 0, 6), 3),
        "alarms": _ranges(scores.alarm, BASELINE_ROWS),
        "watch": _ranges(watch_ranges(scores.score, scores.alarm), BASELINE_ROWS),
        "truth": _ranges(labels),
        "classic_alarms": _ranges(t2q, BASELINE_ROWS),
        "injected": None if injected is None else {**asdict(injected), "label": FAULT_LABELS[injected.kind]},
        "alerts": alerts,
    }


def _hydraulic_payload() -> dict | None:
    path = ARTIFACTS_DIR / "hydraulic_oof.pkl"
    if not path.exists():
        return None
    from .data import load_hydraulic_profile, load_hydraulic_sensor
    from .hydraulic import COMPONENTS

    features, oof = pickle.loads(path.read_bytes())
    profile = load_hydraulic_profile()
    rng = np.random.default_rng(3)
    picks: list[int] = []
    healthy_mask = ((profile.cooler == 100) & (profile.valve == 100) & (profile.pump_leak == 0) & (profile.accumulator == 130))
    picks += list(rng.choice(np.flatnonzero(healthy_mask), 2, replace=False))
    for comp, info in COMPONENTS.items():
        for grade in sorted(info["grades"]):
            if grade == info["healthy"]:
                continue
            others_ok = np.ones(len(profile), bool)
            for other, oinfo in COMPONENTS.items():
                if other != comp:
                    others_ok &= profile[other].to_numpy() == oinfo["healthy"]
            pool = np.flatnonzero((profile[comp].to_numpy() == grade) & others_ok)
            if len(pool) == 0:
                pool = np.flatnonzero(profile[comp].to_numpy() == grade)
            picks.append(int(rng.choice(pool)))
    raw = {s: load_hydraulic_sensor(s) for s in ("PS1", "FS1", "TS1", "EPS1")}
    cycles = []
    for idx in picks:
        comps = {}
        for comp, (pred, proba, classes) in oof.items():
            comps[comp] = {"true": float(profile[comp].iloc[idx]), "pred": float(pred[idx]),
                           "proba": {str(float(c)): round(float(p), 3) for c, p in zip(classes, proba[idx])}}
        traces = {s: _round(raw[s][idx].reshape(120, -1).mean(axis=1) if raw[s].shape[1] % 120 == 0
                            else raw[s][idx], 4) for s in raw}
        degraded = [COMPONENTS[c]["label"] for c in COMPONENTS
                    if profile[c].iloc[idx] != COMPONENTS[c]["healthy"]]
        cycles.append({"cycle": int(idx) + 1, "components": comps, "traces": traces,
                       "summary": ", ".join(degraded) if degraded else "All components healthy"})
    meta = {c: {"label": i["label"], "unit": i["unit"], "healthy": i["healthy"],
                "grades": {str(float(k)): v for k, v in i["grades"].items()}} for c, i in COMPONENTS.items()}
    return {"components": meta, "cycles": cycles}


def export() -> None:
    out = WEB_DATA_DIR
    (out / "runs").mkdir(parents=True, exist_ok=True)
    results = json.loads((RESULTS_DIR / "results.json").read_text())
    config = DetectorConfig(**{k: v for k, v in results["tuning"]["balanced"]["shipped_config"].items()})
    runs = load_runs()
    anomaly_free = load_anomaly_free()
    relations = relationship_models(runs, anomaly_free)

    index = []
    for run in runs:
        payload = run_payload(run, config, relations[run.group], f"SKAB run {run.name}",
                              "Real data from the SKAB pump rig. The shaded band is the fault labelled by SKAB.", "real")
        (out / "runs" / f"{run.name.replace('/', '-')}.json").write_text(json.dumps(payload, separators=(",", ":")))
        first = payload["alerts"][0] if payload["alerts"] else None
        index.append({"id": run.name, "file": f"runs/{run.name.replace('/', '-')}.json", "group": run.group,
                      "verdicts": [a["verdict"] for a in payload["alerts"]],
                      "strength": max(first["contributions"].values()) if first else 0,
                      "lead": first["lead"] if first else None})

    scenarios = []
    chunks = healthy_chunks(anomaly_free)
    # 1. Healthy run with no alarm.
    for chunk in chunks:
        p = run_payload(chunk, config, relations["healthy"], "Healthy run",
                        "Eleven minutes of normal operation from SKAB's anomaly-free recording. "
                        "LineGuard learns the baseline in the first 400 s and then stays quiet.", "real")
        if not p["alerts"]:
            scenarios.append(("healthy", p))
            break
    # 2. Harmless drift: the run where classic T²+Q raises most false alarms that LineGuard avoids.
    best, best_gap = None, -1
    for run in runs:
        y = run.anomaly[BASELINE_ROWS:]
        t2q = baselines.pca_t2_q(run.signals.iloc[:BASELINE_ROWS], run.signals.iloc[BASELINE_ROWS:])
        det = LineGuardDetector(config).fit(run.signals.iloc[:BASELINE_ROWS])
        lg = det.score_run(run.signals).alarm
        gap = int((t2q & ~y).sum()) - int((lg & ~y).sum())
        if gap > best_gap and (lg & y).any():
            best, best_gap = run, gap
    p = run_payload(best, config, relations[best.group], "Harmless drift",
                    f"Real run {best.name}. Temperatures drift and settle at a new level after the fault clears. "
                    "Classic T²+Q control limits keep alarming on that; LineGuard judges thermal signals by their "
                    "rate of change, so the drift is not an alarm.", "real")
    scenarios.append(("drift", p))
    # 3/4. Sensor faults injected into a healthy chunk.
    rng = np.random.default_rng(11)
    for sid, sensor, kind, title, note in (
        ("wire", "Volume Flow RateRMS", "dropout", "Sensor wire fails",
         "Injected fault: the flow meter's signal drops to zero (a broken wire) on healthy data. "
         "LineGuard calls it a sensor fault, not a pump fault."),
        ("loose", "Accelerometer1RMS", "spikes", "Loose sensor contact",
         "Injected fault: intermittent spikes on vibration sensor A. The second vibration sensor on the same "
         "pump stays calm, so LineGuard blames the sensor."),
    ):
        chunk = chunks[2]
        fault = InjectedFault(sensor, kind, 640, 820)
        base_std = float(chunk.signals.iloc[:BASELINE_ROWS][sensor].std())
        signals = inject(chunk.signals.reset_index(drop=True), fault, base_std, rng)
        p = run_payload(chunk, config, relations["healthy"], title, note, "injected", fault, signals)
        p["id"] = f"injected/{sid}"
        scenarios.append((sid, p))
    # 5. Confirmed machine fault and 6. unconfirmed single-signal fault (both real).
    # Clearest real example of each verdict: strongest first alarm, non-thermal lead.
    picked = {}
    for kind in ("machine", "unconfirmed"):
        pool = [e for e in index if e["verdicts"][:1] == [kind] and e["lead"] not in SLOW_SIGNALS]
        if kind == "unconfirmed":
            pool = [e for e in pool if e["group"].startswith("valve")]
        picked[kind] = max(pool, key=lambda e: e["strength"])["id"] if pool else None
    for kind, title, note in (
        ("machine", "Pump fault, confirmed", "Real run {id}. Several related signals leave their envelope together, "
                                              "so LineGuard reports a machine fault and names the signals."),
        ("unconfirmed", "Valve fault, one signal", "Real run {id}. At first only the flow moves. A real valve fault and a "
                                                    "flow-meter offset look identical, so LineGuard says so and asks for a quick check."),
    ):
        if picked[kind]:
            run = next(r for r in runs if r.name == picked[kind])
            p = run_payload(run, config, relations[run.group], title, note.format(id=run.name), "real")
            scenarios.append((kind, p))
    scen_index = []
    for sid, p in scenarios:
        (out / "runs" / f"scenario-{sid}.json").write_text(json.dumps(p, separators=(",", ":")))
        scen_index.append({"id": sid, "title": p["title"], "tag": p["tag"], "file": f"runs/scenario-{sid}.json"})

    # Early-warning statistics over the 34 real runs.
    healthy_s = watch_s = 0
    leads = []
    for run in runs:
        det = LineGuardDetector(config).fit(run.signals.iloc[:BASELINE_ROWS])
        sc = det.score_run(run.signals)
        w = watch_ranges(sc.score, sc.alarm)
        y = run.anomaly[BASELINE_ROWS:]
        healthy_s += int((~y).sum()); watch_s += int((w & ~y).sum())
        for a, b in segments(y):
            first_alarm = next((i for i in range(a, b) if sc.alarm[i]), None)
            first_watch = next((i for i in range(max(0, a - 60), b) if w[i]), None)
            if first_alarm is not None and first_watch is not None and first_watch < first_alarm:
                leads.append(first_alarm - first_watch)
    watch_stats = {"level": WATCH_LEVEL, "seconds": WATCH_SECONDS, "healthy_share": watch_s / healthy_s,
                   "faults_warned_earlier": len(leads), "median_lead_s": float(np.median(leads)) if leads else None}
    slim = {k: results[k] for k in ("protocol", "ladder", "deep", "published", "robustness", "hydraulic",
                                     "line_simulation", "tuning", "skab_crosscheck_balanced_nested")}
    slim["watch"] = watch_stats
    manifest = {
        "signals": [{"key": s, **SIGNAL_INFO[s], "tag": tag} for s, tag in zip(SIGNALS, [
            "VT 101A", "VT 101B", "IT 101", "PT 101", "TT 101A", "TT 101B", "ET 101", "FT 101"])],
        "scenarios": scen_index, "runs": index, "results": slim,
        "config": asdict(config),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":"), default=float))
    hyd = _hydraulic_payload()
    if hyd:
        (out / "hydraulic.json").write_text(json.dumps(hyd, separators=(",", ":")))
    print(f"Exported {len(index)} runs and {len(scen_index)} scenarios to {out}")


if __name__ == "__main__":
    export()
