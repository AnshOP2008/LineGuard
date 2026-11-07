# Submission kit

- Repository: https://github.com/AnshOP2008/LineGuard
- Quick replay: `python -m http.server 8000 --directory web`

---

## 1. CodeQuest 2026: prototype round write-up (paste into the form)

**LineGuard: detect machine abnormalities before they disrupt the production line**

**Problem.** Small and mid-sized plants watch machines with scheduled checks or one alarm limit per sensor. Tight limits fire on harmless drift, loose limits miss early faults, and a broken sensor wire looks like a broken machine. Nobody sees which downstream stations are at risk.

**What we built.** A working, open-source monitoring system, evaluated on the public SKAB pump benchmark (34 real fault runs) and the UCI hydraulic rig (2205 cycles):

1. A strictly real-time detector that judges each signal by what is physically meaningful (level for flow, pressure, vibration; rate of change for temperatures) against a baseline learnt in the first 400 s, with calibrated limits, hysteresis and a data-quality check for stuck or dead sensors.
2. A diagnosis layer: a cross-signal relationship model plus paired-sensor checks gives one of three verdicts (machine fault, sensor fault, or unconfirmed with a two-minute check), with the evidence shown to the operator.
3. Component diagnosis on the hydraulic rig: cooler 99.8 %, valve 100 %, pump 97.2 %, accumulator 86.8 % (blocked cross-validation).
4. Action, not just alerts: a Watch state before the alarm, and a prioritised work order per alert with a checklist and status (Open, Acknowledged, In progress, Closed). Closing is verified against the data.
5. A SimPy line simulation that turns an alert into lost units and idle time downstream.
6. A FastAPI service with an online monitor, SSE stream, MQTT bridge, SQLite alert log and operator feedback that recalibrates limits; a dashboard; Docker; tests; CI.

**Results (nested cross-validation, SKAB's own protocol and metric code).** F1 0.849 vs 0.78 for the best published SKAB leaderboard entry; 31/34 faults caught with a 13 s median delay; 9 false-alarm events vs 125 for classic T²+Q control charts. While reproducing the leaderboard we found that its reference notebooks let a 60 s window look ~58 s into the future; under real-time conditions the best deep model falls to F1 0.77 with a 73 s delay. On 480 injected sensor faults, 82 % of stuck, dead, spiky or noisy sensors are identified as sensor faults (right sensor named every time) while only 6 % of real machine faults are dismissed as sensor faults.

**Honesty.** Sensor faults are injected (SKAB has none labelled) and the line is simulated; both are labelled everywhere. One lab rig, short runs, no plant pilot yet.

**Tech stack.** Python, NumPy, pandas, scikit-learn, LightGBM, PyTorch (baselines), SimPy, FastAPI, SQLite, MQTT (Mosquitto, paho), vanilla JS dashboard, Docker, GitHub Actions.

**Run it.** `pip install -r requirements-dev.txt && python -m lineguard all && python -m lineguard serve`, or open the live dashboard.

---

## 3. Two-minute demo video script

| Time | Screen | Say |
|---|---|---|
| 0:00 | Monitor tab, *Healthy run*, press Play at 20× | "Factories still watch machines with one limit per sensor. LineGuard watches eight sensors on this pump. The first 400 seconds are its healthy baseline, and on a healthy run it stays quiet." |
| 0:15 | *Harmless drift*; point at the grey T²+Q bar vs the empty LineGuard bar | "Here temperatures drift after a fault clears. Classic control charts keep alarming. LineGuard judges temperatures by how fast they change, so drift is not an alarm." |
| 0:35 | *Pump fault, confirmed*; let the alert card appear | "A real fault. The flow drops and a second signal confirms it, so it's a machine fault. Here are the signals that left their limits, what the other sensors expected, the likely cause, and the action. Assembly and Packing light up as at risk." |
| 0:55 | Click *Create work order*, tick a step | "And it doesn't stop at an alert: one click turns it into a P1 work order with the checklist, tracked until it's closed, and LineGuard checks the signals really came back." |
| 1:00 | *Sensor wire fails* | "Now a broken wire on the flow meter, injected into healthy data. A threshold system would send a mechanic. LineGuard sees a dead signal while related sensors are calm, and calls it a sensor fault." |
| 1:15 | *Valve fault, one signal* | "When only one signal moves, a real fault and a sensor offset look identical. LineGuard says so and asks for a two-minute gauge check instead of guessing." |
| 1:25 | Components tab, click a valve cycle | "On a hydraulic rig it also names the worn component and how worn: 99.8 to 100 percent for cooler and valve." |
| 1:35 | Line impact tab, change the alert minute | "And it shows the cost of waiting: about 130 units per shift in this simulated line." |
| 1:45 | Results tab | "On the public SKAB benchmark it beats every published entry, F1 0.85 versus 0.78, with 14 times fewer false alarms than control charts, and we found the leaderboard's deep models peek a minute into the future. Everything is open source and reproducible with one command." |

---

## 4. Likely judge questions

**Can you name the failing component on the pump you demo?** On the SKAB pump the verdict is machine vs sensor plus the signals involved and a likely-cause hint; SKAB has no component labels. Component naming is validated on the UCI hydraulic rig, where labels exist (87-100 %). We show these as two separate capabilities on purpose.

**How is this different from threshold alarms?** Limits are learnt per feature from the healthy baseline, temperatures are judged by rate of change, alarms need confirmation and clear with hysteresis, and every alarm comes with a verdict and evidence. Measured: 9 false-alarm events vs 47 for per-sensor limits and 125 for T²+Q.

**Isn't the sensor-fault data fake?** The sensor faults are injected into real healthy data because no public dataset labels them; they are marked as injected everywhere, and the verdict rule was checked on a fresh random seed. Real machine faults are real SKAB faults.

**Why not just use a deep model?** We re-implemented Conv-AE and LSTM-AE. With SKAB's look-ahead they are competitive; run in real time they lose (F1 0.77, 73 s delay). LineGuard is also explainable to a technician.

**How would this run in a plant?** Sensors → MQTT → the FastAPI service running the same online monitor (tested to match the batch pipeline) → dashboard and SQLite log. It runs on a laptop CPU; the next step is an ONNX export for an edge gateway and a pilot with a local unit.

**What does it cost?** Open source. Hardware is whatever sensors the plant already has plus a small gateway.
