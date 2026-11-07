"""Component diagnosis on the UCI hydraulic test rig (Helwig et al., 2015).

The rig repeats 60 s load cycles while the condition of four components is
varied: cooler, switching valve, pump (internal leakage) and accumulator.
For each cycle we summarise every sensor by its shape over the cycle (mean in
12 equal segments) plus its spread and trend, then train one classifier per
component to name its condition grade.

Validation is blocked: cycles are grouped into contiguous blocks of 50 and
whole blocks are held out, so neighbouring (near-identical) cycles never sit
on both sides of a split.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold

from .data import HYDRAULIC_SENSORS, load_hydraulic_profile, load_hydraulic_sensor

SEGMENTS = 12
BLOCK = 50
COMPONENTS = {
    "cooler": {"label": "Cooler", "unit": "% efficiency", "healthy": 100,
               "grades": {3: "Close to total failure", 20: "Reduced efficiency", 100: "Full efficiency"}},
    "valve": {"label": "Switching valve", "unit": "% switching", "healthy": 100,
              "grades": {73: "Close to total failure", 80: "Severe lag", 90: "Small lag", 100: "Optimal"}},
    "pump_leak": {"label": "Pump", "unit": "internal leakage", "healthy": 0,
                  "grades": {0: "No leakage", 1: "Weak leakage", 2: "Severe leakage"}},
    "accumulator": {"label": "Accumulator", "unit": "bar pre-charge", "healthy": 130,
                    "grades": {130: "Optimal pressure", 115: "Slightly reduced", 100: "Severely reduced",
                               90: "Close to total failure"}},
}


def cycle_features() -> pd.DataFrame:
    blocks = []
    for sensor in HYDRAULIC_SENSORS:
        x = load_hydraulic_sensor(sensor)
        segs = np.array_split(x, SEGMENTS, axis=1)
        cols = {f"{sensor}_seg{i:02d}": s.mean(axis=1) for i, s in enumerate(segs)}
        cols[f"{sensor}_std"] = x.std(axis=1)
        cols[f"{sensor}_range"] = x.max(axis=1) - x.min(axis=1)
        cols[f"{sensor}_trend"] = segs[-1].mean(axis=1) - segs[0].mean(axis=1)
        blocks.append(pd.DataFrame(cols))
    return pd.concat(blocks, axis=1)


def _model(seed: int = 0) -> LGBMClassifier:
    return LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10,
                          subsample=0.8, subsample_freq=1, colsample_bytree=0.5, random_state=seed,
                          verbosity=-1)


def evaluate(features: pd.DataFrame | None = None, folds: int = 5) -> dict:
    features = cycle_features() if features is None else features
    profile = load_hydraulic_profile()
    groups = np.arange(len(features)) // BLOCK
    report: dict = {"protocol": f"GroupKFold({folds}) over contiguous {BLOCK}-cycle blocks", "components": {}}
    oof_all = {}
    for comp, info in COMPONENTS.items():
        y = profile[comp].to_numpy()
        oof = np.zeros_like(y)
        proba = np.zeros((len(y), len(np.unique(y))))
        for train, test in GroupKFold(folds).split(features, y, groups):
            m = _model().fit(features.iloc[train], y[train])
            oof[test] = m.predict(features.iloc[test])
            proba[test] = m.predict_proba(features.iloc[test])
        classes = sorted(np.unique(y))
        healthy = info["healthy"]
        # "Earliest severity caught": recall of "not healthy" for each degraded grade.
        per_grade = {}
        for grade in classes:
            mask = y == grade
            if grade == healthy:
                per_grade[str(grade)] = {"flagged_as_degraded": float((oof[mask] != healthy).mean()), "n": int(mask.sum())}
            else:
                per_grade[str(grade)] = {"flagged_as_degraded": float((oof[mask] != healthy).mean()), "n": int(mask.sum())}
        report["components"][comp] = {
            "label": info["label"],
            "accuracy": float(accuracy_score(y, oof)),
            "macro_f1": float(f1_score(y, oof, average="macro")),
            "classes": [float(c) for c in classes],
            "confusion": confusion_matrix(y, oof, labels=classes).tolist(),
            "per_grade": per_grade,
        }
        oof_all[comp] = (oof, proba, classes)
    return report, oof_all


def fit_final(features: pd.DataFrame) -> dict[str, LGBMClassifier]:
    profile = load_hydraulic_profile()
    return {comp: _model().fit(features, profile[comp].to_numpy()) for comp in COMPONENTS}
