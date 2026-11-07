"""Line-impact simulation (SimPy). Every output here is SIMULATED.

A four-stage line (Cutting -> Machining -> Assembly -> Packing) with finite
buffers. The monitored pump feeds Machining. When LineGuard raises an alert we
compare two decisions over one shift:

* inspect now   : a short planned stop at the alert time;
* run to failure: keep running; parts made while degraded are partly scrap,
                  and the machine fails at an uncertain later time, causing a
                  longer unplanned stop. Downstream stages starve while
                  Machining is down and the buffers empty.

All assumptions are explicit parameters and the dashboard lets you edit them.
Results are Monte-Carlo averages, not measurements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import simpy

STAGES = ["Cutting", "Machining", "Assembly", "Packing"]


@dataclass
class LineAssumptions:
    shift_minutes: float = 480
    cycle_minutes: tuple = (0.9, 1.0, 0.95, 0.8)   # per stage
    buffer_capacity: int = 15
    alert_minute: float = 60
    planned_stop_minutes: float = 25               # inspect now
    failure_after_minutes: tuple = (90, 240)       # uniform range after alert
    unplanned_stop_minutes: tuple = (90, 180)      # uniform range
    scrap_rate_degraded: float = 0.12              # share of Machining output scrapped while degraded
    value_per_unit: float = 850.0                  # INR margin per good unit
    downtime_cost_per_minute: float = 1200.0       # INR per minute of idle line staff/overhead


def _simulate(a: LineAssumptions, policy: str, rng: np.random.Generator) -> dict:
    env = simpy.Environment()
    buffers = [simpy.Store(env, capacity=a.buffer_capacity) for _ in range(len(STAGES) - 1)]
    idle = {s: 0.0 for s in STAGES}
    good = {"units": 0, "scrap": 0}
    state = {"machining_down_until": -1.0, "degraded_from": a.alert_minute, "repaired": False}

    if policy == "inspect_now":
        state["down_windows"] = [(a.alert_minute, a.alert_minute + a.planned_stop_minutes)]
        state["degraded_until"] = a.alert_minute
    else:
        fail = a.alert_minute + rng.uniform(*a.failure_after_minutes)
        stop = rng.uniform(*a.unplanned_stop_minutes)
        state["down_windows"] = [(fail, fail + stop)]
        state["degraded_until"] = fail

    def down(t):
        return any(s <= t < e for s, e in state["down_windows"])

    def stage(i):
        name = STAGES[i]
        while True:
            start = env.now
            if i > 0:
                item = yield buffers[i - 1].get()
                idle[name] += env.now - start
            else:
                item = 1
            if i == 1:
                while down(env.now):
                    t0 = env.now
                    yield env.timeout(1)
                    idle[name] += env.now - t0
                if state["degraded_from"] <= env.now < state["degraded_until"] and rng.random() < a.scrap_rate_degraded:
                    item = 0  # scrap: produced but not good
            yield env.timeout(a.cycle_minutes[i])
            if i < len(STAGES) - 1:
                t0 = env.now
                yield buffers[i].put(item)
                idle[name] += env.now - t0
            else:
                good["units"] += item
                good["scrap"] += 1 - item

    for i in range(len(STAGES)):
        env.process(stage(i))
    env.run(until=a.shift_minutes)
    return {"good_units": good["units"], "scrap_units": good["scrap"], "idle_minutes": idle}


def compare(a: LineAssumptions | None = None, runs: int = 300, seed: int = 0) -> dict:
    a = a or LineAssumptions()
    rng = np.random.default_rng(seed)
    out = {}
    for policy in ("inspect_now", "run_to_failure"):
        sims = [_simulate(a, policy, rng) for _ in range(runs)]
        good = np.array([s["good_units"] for s in sims])
        scrap = np.array([s["scrap_units"] for s in sims])
        idle = {st: float(np.mean([s["idle_minutes"][st] for s in sims])) for st in STAGES}
        downstream_idle = idle["Assembly"] + idle["Packing"]
        value = good.mean() * a.value_per_unit - downstream_idle * a.downtime_cost_per_minute
        out[policy] = {
            "good_units_mean": float(good.mean()),
            "good_units_p10": float(np.percentile(good, 10)),
            "good_units_p90": float(np.percentile(good, 90)),
            "scrap_units_mean": float(scrap.mean()),
            "idle_minutes": idle,
            "net_value_inr": float(value),
        }
    i, r = out["inspect_now"], out["run_to_failure"]
    out["summary"] = {
        "units_saved_by_inspecting": i["good_units_mean"] - r["good_units_mean"],
        "scrap_avoided": r["scrap_units_mean"] - i["scrap_units_mean"],
        "value_protected_inr": (i["good_units_mean"] - r["good_units_mean"]) * a.value_per_unit
        + ((r["idle_minutes"]["Assembly"] + r["idle_minutes"]["Packing"])
           - (i["idle_minutes"]["Assembly"] + i["idle_minutes"]["Packing"])) * a.downtime_cost_per_minute,
    }
    out["assumptions"] = asdict(a)
    out["label"] = "SIMULATED: Monte-Carlo average under editable assumptions, not a measurement"
    return out


def inspection_priority(alerts: list[dict]) -> list[dict]:
    """Rank alerts by risk x impact. risk = confidence x severity (0-1),
    impact = share of downstream line that depends on the machine."""
    ranked = []
    for a in alerts:
        risk = a.get("confidence", 0.5) * min(1.0, a.get("severity", 1.0) / 3)
        impact = a.get("downstream_share", 0.75)
        ranked.append({**a, "priority_score": round(risk * impact * 100, 1)})
    return sorted(ranked, key=lambda r: -r["priority_score"])
