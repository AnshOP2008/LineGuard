"""Online (one reading at a time) version of the LineGuard pipeline.

The batch code and this class share the same detector, features, data-quality
check and verdict rule; ``tests/test_online.py`` checks that streaming a run
reading by reading gives the same alarms as scoring it in one go.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import BASELINE_ROWS, DROPOUT_SECONDS, SIGNALS, STUCK_LIMITS, DetectorConfig
from .detectors.lineguard import LineGuardDetector
from .isolation import WINDOW, alarm_evidence, classify
from .relationships import RelationshipModel, relative_levels


@dataclass
class OnlineMonitor:
    config: DetectorConfig
    relations: RelationshipModel
    limit_multipliers: dict[str, float] = field(default_factory=dict)  # from operator feedback
    rows: list[list[float]] = field(default_factory=list)
    detector: LineGuardDetector | None = None
    active: bool = False
    streak: int = 0
    alarm_start: int | None = None
    alert_sent: bool = False
    same_run: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SIGNALS})
    zero_run: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SIGNALS})
    alarm_history: list[bool] = field(default_factory=list)
    quality_history: list[dict[str, str]] = field(default_factory=list)

    @property
    def t(self) -> int:
        return len(self.rows) - 1

    def _frame(self, start: int = 0, stop: int | None = None) -> pd.DataFrame:
        return pd.DataFrame(self.rows[start:stop], columns=SIGNALS, index=range(start, stop or len(self.rows)))

    def _history(self) -> int:
        return self.config.window + self.config.slope_lag + 5

    def push(self, reading: dict[str, float]) -> dict:
        values = [float(reading[s]) for s in SIGNALS]
        prev = self.rows[-1] if self.rows else None
        self.rows.append(values)
        t = self.t
        quality = {}
        for i, s in enumerate(SIGNALS):
            self.same_run[s] = self.same_run[s] + 1 if prev is not None and values[i] == prev[i] else 0
            self.zero_run[s] = self.zero_run[s] + 1 if values[i] == 0 else 0
            if self.zero_run[s] >= DROPOUT_SECONDS:
                quality[s] = "dropout"
            elif self.same_run[s] >= STUCK_LIMITS[s]:
                quality[s] = "stuck"
        event: dict = {"t": t, "reading": reading}
        if t < BASELINE_ROWS:
            event["state"] = "learning"
            return event
        if self.detector is None:
            self.detector = LineGuardDetector(self.config).fit(self._frame(0, BASELINE_ROWS))
            self._apply_feedback()
        tail = self._frame(max(0, t + 1 - self._history()))
        norm = self.detector.normalise(tail).iloc[-1]
        contrib = norm.groupby(lambda c: c.split("::")[0]).max().reindex(SIGNALS)
        score = float(contrib.max())
        # Same hysteresis + confirmation logic as LineGuardDetector.alarm_from_score.
        if self.active:
            self.active = score > self.config.release
            self.streak = 0
        else:
            self.streak = self.streak + 1 if score > 1.0 else 0
            self.active = self.streak >= self.config.min_duration
        alarm = self.active or bool(quality)
        self.alarm_history.append(alarm)
        self.quality_history.append(quality)
        if alarm and self.alarm_start is None:
            self.alarm_start, self.alert_sent = t, False
        event.update(state="alarm" if alarm else "normal", score=score,
                     contrib={k: float(v) for k, v in contrib.items()}, quality=quality)
        if self.alarm_start is not None and not self.alert_sent and (t - self.alarm_start + 1 >= WINDOW or not alarm):
            if t - self.alarm_start + 1 >= 10 or not alarm:
                event["alert"] = self._verdict(self.alarm_start, t + 1)
                self.alert_sent = True
        if not alarm:
            self.alarm_start = None
        return event

    def _verdict(self, start: int, stop: int) -> dict:
        from .export import _alert_payload  # shared wording with the dashboard

        signals = self._frame(0, stop)
        normalised = self.detector.normalise(signals)
        levels = relative_levels(signals)
        quality = pd.DataFrame([{s: q.get(s, "") for s in SIGNALS} for q in self.quality_history],
                               index=range(BASELINE_ROWS, BASELINE_ROWS + len(self.quality_history)))
        ev = alarm_evidence(signals, normalised, levels, self.relations, start, stop, quality)
        return _alert_payload(ev, classify(ev), start, stop, "Live data: no ground truth.")

    def _apply_feedback(self) -> None:
        """Operator feedback widens (false alarms) or keeps limits per signal."""
        if not self.limit_multipliers:
            return
        names = self.detector.feature_names_
        mult = np.array([self.limit_multipliers.get(n.split("::")[0], 1.0) for n in names])
        self.detector.limit_ = self.detector.limit_ * mult
