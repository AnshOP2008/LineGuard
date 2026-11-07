"""Regenerate every number LineGuard reports.

    python -m lineguard evaluate            # fast: everything except deep models
    python -m lineguard evaluate --deep     # also train Conv-AE / LSTM-AE (3 seeds each)

Writes results/results.json (machine readable, used by the dashboard and
README) and results/RESULTS.md (human readable). Nothing is typed by hand.
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .config import ARTIFACTS_DIR, BASELINE_ROWS, RESULTS_DIR, SKAB_DIR
from .data import load_anomaly_free, load_runs
from .detectors import baselines
from .detectors.lineguard import fit_and_score
from .metrics import events, pointwise
from .tuning import nested_cv

# SKAB "proposed leaderboard", outlier-detection problem (README of waico/SKAB).
PUBLISHED = [
    ("Conv-AE", 0.78, 13.55, 28.02),
    ("MSET", 0.78, 39.73, 14.13),
    ("T-squared + Q (PCA)", 0.76, 26.62, 24.92),
    ("LSTM-AE", 0.74, 29.96, 25.92),
    ("T-squared", 0.66, 19.21, 42.60),
    ("LSTM-VAE", 0.56, 9.13, 55.03),
    ("Vanilla LSTM", 0.54, 12.54, 59.53),
    ("Vanilla AE", 0.39, 2.59, 75.15),
    ("MSCRED", 0.36, 49.94, 69.88),
    ("Isolation forest", 0.29, 2.56, 82.89),
]
DEEP_CACHE = ARTIFACTS_DIR / "deep_scores.pkl"


def _summary(truths, preds) -> dict:
    return {**pointwise(truths, preds), **events(truths, preds)}


def skab_crosscheck(runs, preds) -> dict | None:
    """Score with SKAB's own metric code (from the downloaded SKAB repo) when available."""
    core = SKAB_DIR / "core"
    if not (core / "metrics.py").exists():
        return None
    import io
    import contextlib

    import pandas as pd

    sys.path.insert(0, str(SKAB_DIR))
    try:
        from core.metrics import chp_score  # type: ignore
    finally:
        sys.path.pop(0)
    true_o, pred_o, true_cp, pred_cp = [], [], [], []
    for run, p in zip(runs, preds):
        idx = pd.DatetimeIndex(run.frame["datetime"].iloc[BASELINE_ROWS:])
        y = pd.Series(run.frame["anomaly"].iloc[BASELINE_ROWS:].to_numpy(), index=idx)
        pr = pd.Series(p.astype(int), index=idx)
        cp = pd.Series(run.frame["changepoint"].iloc[BASELINE_ROWS:].to_numpy(), index=idx)
        pcp = pr.diff().abs().fillna(pr.iloc[0])
        true_o.append(y), pred_o.append(pr), true_cp.append(cp), pred_cp.append(pcp)
    with contextlib.redirect_stdout(io.StringIO()):
        f1, far, mar = chp_score(true_o, pred_o, metric="binary")
        nab = chp_score(true_cp, pred_cp, metric="nab", window_width="60s", anomaly_window_destination="righter")
    return {"f1": f1, "far": far, "mar": mar, "nab": nab}


def deep_results(runs, truths, train: bool) -> dict:
    from .detectors.deep import STEPS, autoencoder_scores, skab_rule

    cache = pickle.loads(DEEP_CACHE.read_bytes()) if DEEP_CACHE.exists() else {}
    if train:
        for kind in ("conv", "lstm"):
            for seed in (0, 1, 2):
                if (kind, seed) not in cache:
                    cache[(kind, seed)] = [
                        autoencoder_scores(kind, r.signals.iloc[:BASELINE_ROWS], r.signals.iloc[BASELINE_ROWS:], seed=seed)
                        for r in runs]
                    DEEP_CACHE.write_bytes(pickle.dumps(cache))
    out = {}
    for kind, label in (("conv", "Conv-AE"), ("lstm", "LSTM-AE")):
        seeds = sorted(s for k, s in cache if k == kind)
        if not seeds:
            continue
        for mode in ("as_published", "causal"):
            rows = []
            for seed in seeds:
                preds = []
                for ratio in cache[(kind, seed)]:
                    flagged = skab_rule(ratio)
                    if mode == "causal":
                        # The SKAB notebook aligns a 60 s window to its first row, so a flag
                        # at t uses data up to t+58 s. Causal = shift to the last row used.
                        shift = STEPS - 2
                        flagged = np.r_[np.zeros(shift, bool), flagged[:-shift]]
                    preds.append(flagged)
                rows.append(_summary(truths, preds))
            keys = ["f1", "far", "mar", "recall", "median_delay_s", "false_alarm_events"]
            out[f"{label} ({'look-ahead, as published' if mode == 'as_published' else 'causal'})"] = {
                "mean": {k: float(np.nanmean([r[k] for r in rows])) for k in keys},
                "std": {k: float(np.nanstd([r[k] for r in rows])) for k in keys},
                "seeds": len(seeds),
                "causal": mode == "causal",
            }
    return out


def run(deep: bool = False) -> dict:
    t0 = time.time()
    RESULTS_DIR.mkdir(exist_ok=True)
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    runs = load_runs()
    anomaly_free = load_anomaly_free()
    truths = [r.anomaly[BASELINE_ROWS:] for r in runs]
    report: dict = {"protocol": {
        "dataset": "SKAB v0.9: 34 runs, 8 signals at 1 Hz",
        "split": "SKAB protocol: first 400 s of each run = normal-only baseline, remainder = test",
        "pointwise": "SKAB binary metric aggregated over all runs (F1 = TP/(TP+(FP+FN)/2))",
        "events": "fault detected if an alarm is active inside its labelled window; delay from window start; "
                  "an alarm that never touches a fault is one false-alarm event",
        "tuning": "nested leave-one-family-out (valve1 / valve2 / other) over a 288-setting grid",
    }}

    # ---------------------------------------------------------------- baselines
    ladder = {}
    for name, fn in (("Per-sensor limits (z-score)", baselines.rolling_zscore),
                     ("PCA T² + Q", baselines.pca_t2_q),
                     ("Isolation Forest", baselines.isolation_forest)):
        preds = [fn(r.signals.iloc[:BASELINE_ROWS], r.signals.iloc[BASELINE_ROWS:]) for r in runs]
        ladder[name] = {**_summary(truths, preds), "causal": True}
    report["classic_t2q_alarms"] = {r.name: p.tolist() for r, p in zip(runs, [
        baselines.pca_t2_q(r.signals.iloc[:BASELINE_ROWS], r.signals.iloc[BASELINE_ROWS:]) for r in runs])}

    # ---------------------------------------------------------------- LineGuard
    cv = nested_cv(runs)
    shipped = {}
    for mode in ("balanced", "low_false_alarm"):
        res = cv[mode]
        ladder[f"LineGuard, {mode.replace('_', '-')} (nested CV)"] = {
            **_summary(truths, res["nested_predictions"]), "causal": True, "ours": True}
        shipped[mode] = res["shipped_config"]
    report["tuning"] = {mode: {"chosen_per_fold": cv[mode]["chosen_per_fold"],
                               "shipped_config": asdict(cv[mode]["shipped_config"]),
                               "shipped_in_sample": cv[mode]["shipped_in_sample"]}
                        for mode in ("balanced", "low_false_alarm")}
    report["tuning"]["n_configs"] = cv["n_configs"]
    shipped_preds = [fit_and_score(r.signals, shipped["balanced"]).alarm for r in runs]
    report["skab_crosscheck_balanced_in_sample"] = skab_crosscheck(runs, shipped_preds)
    report["skab_crosscheck_balanced_nested"] = skab_crosscheck(runs, cv["balanced"]["nested_predictions"])

    report["deep"] = deep_results(runs, truths, train=deep)
    report["ladder"] = ladder
    report["published"] = [{"model": m, "f1": f, "far": a, "mar": b} for m, f, a, b in PUBLISHED]

    # ---------------------------------------------------------------- robustness
    from .robustness import study
    report["robustness"] = study(runs, anomaly_free, shipped["balanced"], seed=1)

    # ---------------------------------------------------------------- hydraulic
    try:
        from .hydraulic import cycle_features, evaluate as hydraulic_eval
        features = cycle_features()
        hyd, oof = hydraulic_eval(features)
        report["hydraulic"] = hyd
        (ARTIFACTS_DIR / "hydraulic_oof.pkl").write_bytes(pickle.dumps((features, oof)))
    except FileNotFoundError:
        report["hydraulic"] = None

    # ---------------------------------------------------------------- simulation
    from .simulation import compare
    report["line_simulation"] = compare(runs=300)

    (ARTIFACTS_DIR / "shipped_configs.json").write_text(json.dumps({k: asdict(v) for k, v in shipped.items()}, indent=2))
    report["runtime_s"] = round(time.time() - t0, 1)
    (RESULTS_DIR / "results.json").write_text(json.dumps(report, indent=2, default=float))
    (RESULTS_DIR / "RESULTS.md").write_text(render_markdown(report))
    return report


def _fmt(v, digits=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def render_markdown(r: dict) -> str:
    lines = ["# LineGuard results", "",
             "Generated by `python -m lineguard evaluate`. Do not edit by hand.", "",
             "## SKAB anomaly detection (34 runs, official 400-row baseline protocol)", "",
             "| Model | F1 ↑ | FAR % ↓ | MAR % ↓ | Faults caught | Median delay (s) | False-alarm events | Causal |",
             "|---|---:|---:|---:|---:|---:|---:|:---:|"]
    for name, m in r["ladder"].items():
        lines.append(f"| {'**' + name + '**' if m.get('ours') else name} | {m['f1']:.3f} | {m['far']:.1f} | {m['mar']:.1f} | "
                     f"{m['detected']}/{m['faults']} | {_fmt(m['median_delay_s'], 0)} | {m['false_alarm_events']} | yes |")
    for name, m in r["deep"].items():
        mu, sd = m["mean"], m["std"]
        lines.append(f"| {name}, our re-implementation ({m['seeds']} seeds) | {mu['f1']:.3f} ± {sd['f1']:.3f} | {mu['far']:.1f} | "
                     f"{mu['mar']:.1f} | {mu['recall'] * 34:.0f}/34 | {_fmt(mu['median_delay_s'], 0)} | "
                     f"{mu['false_alarm_events']:.1f} | {'yes' if m['causal'] else 'no'} |")
    lines += ["", "Published SKAB leaderboard (outlier detection), for reference:", "",
              "| Model | F1 | FAR % | MAR % |", "|---|---:|---:|---:|"]
    lines += [f"| {p['model']} | {p['f1']:.2f} | {p['far']:.2f} | {p['mar']:.2f} |" for p in r["published"]]
    cc = r.get("skab_crosscheck_balanced_nested")
    if cc:
        lines += ["", f"Cross-check with SKAB's own metric code (nested CV predictions): F1 {cc['f1']}, "
                      f"FAR {cc['far']} %, MAR {cc['mar']} %, NAB (standard / LowFP / LowFN) "
                      f"{' / '.join(f'{float(v):.1f}' for v in (cc['nab'].values() if isinstance(cc['nab'], dict) else cc['nab'][:3]))} "
                      "(SKAB changepoint leaderboard: best published 32.42, Conv-AE 23.61, T²+Q 25.35)."]
    rb = r["robustness"]
    lines += ["", "## Sensor faults vs machine faults", "",
              "| Truth | Verdict: machine | Verdict: sensor | Verdict: unconfirmed |", "|---|---:|---:|---:|"]
    for kind, row in rb["verdicts"].items():
        lines.append(f"| {kind} | {row['machine']} | {row['sensor']} | {row['unconfirmed']} |")
    det = rb["detection"]
    lines += ["", "Injected sensor faults detected: " + ", ".join(
        f"{k} {v['detected']}/{v['injected']}" for k, v in det.items()) + ".",
              f"Real machine faults dismissed as sensor faults: {rb['machine_dismissed_as_sensor'] * 100:.0f}%. "
              f"Sensor faults with a clear signature (stuck, dropout, spikes, noise) identified as sensor faults: "
              f"{rb['signature_faults_called_sensor'] * 100:.0f}%. Correct sensor named: {rb['correct_sensor_named'] * 100:.0f}%."]
    if r.get("hydraulic"):
        lines += ["", "## Component diagnosis (UCI hydraulic rig, 2205 cycles)", "",
                  f"Protocol: {r['hydraulic']['protocol']}.", "",
                  "| Component | Accuracy | Macro F1 |", "|---|---:|---:|"]
        for comp in r["hydraulic"]["components"].values():
            lines.append(f"| {comp['label']} | {comp['accuracy'] * 100:.1f}% | {comp['macro_f1']:.3f} |")
    s = r["line_simulation"]["summary"]
    lines += ["", "## Line impact (SIMULATED)", "",
              f"Inspecting at the alert instead of running to failure: {s['units_saved_by_inspecting']:.0f} more good units per shift, "
              f"{s['scrap_avoided']:.0f} scrap units avoided, about INR {s['value_protected_inr']:,.0f} protected "
              "(default assumptions, Monte-Carlo average)."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run(deep="--deep" in sys.argv)
