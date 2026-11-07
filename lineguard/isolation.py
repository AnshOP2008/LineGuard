"""Sensor fault vs machine fault: the step that stops a loose wire triggering a
maintenance callout.

For every confirmed alarm we look at its first ``WINDOW`` seconds (what the
operator would see when it fires) and compute interpretable evidence:

* how many signals deviate, and how concentrated the deviation is;
* whether the deviating signal is *supported* by the other sensors
  (relationship-model residual vs raw deviation);
* signal-quality checks on the leading signal: variability collapse (stuck),
  zeros (signal lost), jumps (loose contact), excess noise.

A transparent rule turns that evidence into one of three verdicts (see
``classify``): machine fault (corroborated by a related sensor), sensor fault
(an instrument signature while related sensors stay calm), or unconfirmed
(a single-signal shift: physically indistinguishable, so check the sensor
first). The rule is evaluated on real SKAB faults and injected sensor faults.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import BASELINE_ROWS, SIGNAL_INFO, SIGNALS
from .features import feature_signal
from .metrics import segments
from .relationships import RelationshipModel

WINDOW = 30            # seconds of evidence used for the verdict
QUALITY_WINDOW = 120   # data-quality flags are checked over the first two minutes of the alarm

EVIDENCE_COLUMNS = [
    "lead_contribution", "second_contribution", "lead_share", "n_signals_over_limit",
    "lead_deviation_z", "lead_explained", "others_max_deviation_z",
    "lead_variability_ratio", "lead_jump_ratio", "lead_zero_fraction", "lead_flat_fraction",
    "min_variability_ratio", "max_jump_ratio",
]


@dataclass
class AlarmEvidence:
    start: int
    end: int
    lead_signal: str
    contributions: dict[str, float]
    features: dict[str, float]
    predicted: dict[str, float]      # relationship model expectation (z) per signal
    observed: dict[str, float]       # observed level (z) per signal
    quality_flags: dict[str, str]    # signal -> 'stuck' | 'dropout' from the data-quality check
    variability: dict[str, float]    # window std / baseline std per signal
    jumps: dict[str, float]          # max step / baseline 99.9th pct step per signal


def baseline_quality(signals: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    base = signals[SIGNALS].iloc[:BASELINE_ROWS]
    std = base.std() + 1e-9
    jump = base.diff().abs().quantile(0.999) + 1e-9
    return std, jump


def alarm_evidence(signals: pd.DataFrame, normalised: pd.DataFrame, levels: pd.DataFrame,
                   relations: RelationshipModel, start: int, end: int,
                   quality: pd.DataFrame | None = None) -> AlarmEvidence:
    """Evidence for an alarm spanning absolute rows [start, end).

    ``normalised`` and ``levels`` are the detector's normalised features and the
    baseline-relative levels for the whole run (absolute row index)."""
    stop = max(min(end, start + WINDOW), min(start + 10, len(signals)))
    window_norm = normalised.iloc[start:stop]
    contrib = window_norm.T.groupby(feature_signal).max().T.mean().reindex(SIGNALS).fillna(0)
    order = contrib.sort_values(ascending=False)
    lead = order.index[0]

    z = levels.iloc[start:stop].clip(-8, 8)
    predicted = relations.predict(z)
    residual = z - predicted
    lead_dev = float(np.abs(z[lead]).mean())
    lead_err = float(np.abs(residual[lead]).mean())
    explained = 1 - lead_err / (lead_dev + 0.5)
    others = z.drop(columns=lead)

    raw = signals[SIGNALS].iloc[start:stop]
    base_std, base_jump = baseline_quality(signals)
    var_ratio = raw.std().fillna(0) / base_std
    jump_ratio = raw.diff().abs().max().fillna(0) / base_jump
    zero_frac = (raw.abs() < 1e-9).mean()
    flat_frac = (raw.diff().abs() < 1e-12).iloc[1:].mean()

    features = {
        "lead_contribution": float(order.iloc[0]),
        "second_contribution": float(order.iloc[1]),
        "lead_share": float(order.iloc[0] / (order.sum() + 1e-9)),
        "n_signals_over_limit": float((order > 1).sum()),
        "lead_deviation_z": lead_dev,
        "lead_explained": float(explained),
        "others_max_deviation_z": float(np.abs(others).mean().max()),
        "lead_variability_ratio": float(var_ratio[lead]),
        "lead_jump_ratio": float(jump_ratio[lead]),
        "lead_zero_fraction": float(zero_frac[lead]),
        "lead_flat_fraction": float(flat_frac[lead]),
        "min_variability_ratio": float(var_ratio.min()),
        "max_jump_ratio": float(jump_ratio.max()),
        # Heavy-tailed steps (high kurtosis) = isolated spikes; Gaussian-like = broadband noise.
        "lead_step_kurtosis": float(pd.Series(raw[lead].diff().dropna().to_numpy()).kurt()) if stop - start > 5 else 0.0,
    }
    flags = {}
    if quality is not None:
        q = quality.reindex(range(start, min(end, start + QUALITY_WINDOW))).fillna("")
        for col in SIGNALS:
            kinds = [k for k in q[col].unique() if k]
            if kinds:
                flags[col] = kinds[0]
    if flags:
        lead = next(iter(flags))
    return AlarmEvidence(
        start=start, end=end, lead_signal=lead,
        contributions={k: float(v) for k, v in contrib.items()},
        features=features,
        predicted={k: float(v) for k, v in predicted.mean().items()},
        observed={k: float(v) for k, v in z.mean().items()},
        quality_flags=flags,
        variability={k: float(v) for k, v in var_ratio.items()},
        jumps={k: float(v) for k, v in jump_ratio.items()},
    )


def alarm_events(alarm: np.ndarray, offset: int = BASELINE_ROWS) -> list[tuple[int, int]]:
    return [(s + offset, e + offset) for s, e in segments(alarm)]


# Physically related sensors on the SKAB pump rig. Vibration and temperature
# are measured twice; pressure, flow and motor current all respond to the
# hydraulic load; current and voltage share the motor supply.
PARTNERS: dict[str, list[str]] = {
    "Accelerometer1RMS": ["Accelerometer2RMS"],
    "Accelerometer2RMS": ["Accelerometer1RMS"],
    "Temperature": ["Thermocouple"],
    "Thermocouple": ["Temperature"],
    "Pressure": ["Volume Flow RateRMS", "Current"],
    "Volume Flow RateRMS": ["Pressure", "Current"],
    "Current": ["Voltage", "Pressure", "Volume Flow RateRMS"],
    "Voltage": ["Current"],
}

# Rule thresholds, in the detector's own units (1.0 = the signal's alarm limit;
# z = baseline standard deviations; ratios relative to the healthy baseline).
CORROBORATE_LIMIT = 1.0
CORROBORATE_Z = 3.0
NOISE_VARIABILITY = 2.0
SPIKE_JUMP = 3.0
SENSOR_MAX_LEVEL_Z = 3.0
PARTNER_CALM_VARIABILITY = 1.5
PARTNER_CALM_LIMIT = 0.5


def _name(signal: str) -> str:
    """'Flow rate' -> 'flow rate', 'Vibration B' -> 'vibration B'."""
    first, *rest = SIGNAL_INFO[signal]["label"].split(" ")
    return " ".join([first.lower(), *rest])


@dataclass
class Verdict:
    kind: str                 # machine | sensor | unconfirmed
    fault_type: str | None    # for sensor verdicts: stuck | dropout | erratic
    reasons: list[str]
    partner: str
    partner_deviation_z: float


def classify(ev: AlarmEvidence) -> Verdict:
    """Transparent rule: corroboration => machine; instrument signature with a
    calm partner => sensor; otherwise unconfirmed (check the sensor first)."""
    lead = ev.lead_signal
    partners = PARTNERS[lead]
    partner = max(partners, key=lambda p: ev.contributions[p])
    p_limit = max(ev.contributions[p] for p in partners)
    p_dev = max(abs(ev.observed[p]) for p in partners)
    p_var = max(ev.variability[p] for p in partners)
    n_over = sum(v > 1 for v in ev.contributions.values())
    lead_dev = abs(ev.observed[lead])
    lead_var = ev.variability[lead]
    lead_jump = ev.jumps[lead]

    if ev.quality_flags:
        kind = next(iter(ev.quality_flags.values()))
        what = "repeats the same value" if kind == "stuck" else "reads exactly zero"
        return Verdict("sensor", kind, [f"Data-quality check: the sensor {what}, which a working sensor on this rig never does."],
                       partner, p_dev)
    if p_limit > CORROBORATE_LIMIT or n_over >= 2 or p_dev >= CORROBORATE_Z:
        reasons = []
        if p_limit > CORROBORATE_LIMIT or p_dev >= CORROBORATE_Z:
            reasons.append(f"The related {_name(partner)} signal moved too, so the change is physical.")
        else:
            # Corroborated by another signal outside its own limit.
            partner = max((s for s in ev.contributions if s != lead), key=lambda s: ev.contributions[s])
            p_dev = abs(ev.observed[partner])
        if n_over >= 2:
            reasons.append(f"{n_over} signals are outside their limits at the same time, which a single faulty sensor cannot cause.")
        return Verdict("machine", None, reasons, partner, p_dev)
    calm_partner = p_var < PARTNER_CALM_VARIABILITY and p_limit < PARTNER_CALM_LIMIT
    if calm_partner and lead_dev < SENSOR_MAX_LEVEL_Z and (lead_jump >= SPIKE_JUMP or lead_var >= NOISE_VARIABILITY):
        return Verdict("sensor", "erratic", [
            f"The reading became erratic ({lead_var:.1f}x normal variability, jumps {lead_jump:.1f}x the largest healthy step).",
            f"The related {_name(partner)} signal stayed calm, which a real machine change would rarely allow.",
        ], partner, p_dev)
    return Verdict("unconfirmed", None, [
        f"Only {_name(lead)} moved; no related signal confirms it yet.",
        "A real fault and a sensor offset look the same at this point.",
    ], partner, p_dev)
