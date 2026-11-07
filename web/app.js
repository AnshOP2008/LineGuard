/* LineGuard dashboard: replays exported pipeline output (web/data) and, when
   served by the LineGuard service, can stream live data from its API. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
    return el;
  };
  const svgNS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs = {}, ...kids) => {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    for (const kid of kids.flat()) if (kid) el.append(kid);
    return el;
  };
  const fmtTime = (sec) => {
    sec = Math.max(0, Math.round(sec));
    return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
  };
  const pct = (v, d = 0) => `${(v * 100).toFixed(d)}%`;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };


  // ------------------------------------------------------------------ glossary ("i" buttons)
  const GLOSSARY = {
    baseline: ["Healthy baseline", "The first 400 seconds after the pump starts. LineGuard assumes the machine is healthy then and learns what normal looks like for every signal. All alarm limits come from this period. SKAB's benchmark uses the same rule, so results are comparable."],
    limit: ["Alarm limit (1.0)", "Each signal gets its own limit from the baseline: the most extreme value seen while healthy, times a safety margin of 2.5. A value of 1.0 means the signal is exactly at its limit. 2.0 means it is twice as far from normal as the limit allows."],
    health: ["Health score", "At every second, LineGuard compares each signal with its own limit and takes the worst one. Above 1.0 means at least one signal is outside anything seen while the pump was healthy. An alarm starts when the score crosses 1.0 and ends only when it drops below 0.6, so it does not flicker on and off (this is called hysteresis)."],
    sigma: ["σ (sigma)", "The normal amount of wobble a signal had during the healthy baseline (its standard deviation). −3σ means three times that wobble below the usual value. A healthy machine rarely goes beyond about 3σ."],
    expected: ["Expected from the other sensors", "LineGuard's relationship model learnt from healthy data how the eight sensors move together, for example how flow relates to pressure and motor current. It predicts each sensor from the other seven. A big gap between measured and expected means the sensors disagree with each other."],
    variability: ["Variability", "How much the signal jumps around during the first 30 seconds of the alarm, compared with the healthy baseline. 1× is normal. Very high values suggest turbulence, cavitation or a loose connection; values near 0× suggest a frozen sensor."],
    verdict: ["Three verdicts", "Machine fault: the deviation is confirmed by a related sensor or by several signals at once, so the change is physical. Sensor fault: the reading is frozen, dead, spiky or noisy while related sensors stay calm. Unconfirmed: only one signal moved. A real fault and a sensor offset look identical at that point, so LineGuard asks for a quick check instead of guessing."],
    confidence: ["Confidence", "A plain rule, not a probability. High: several signals agree, or a data-quality check failed. Medium: one piece of supporting evidence. Low: only one signal moved."],
    behaviour: ["Signal behaviour", "Simple checks on the raw readings of the leading signal: does it still change (a frozen sensor repeats one value), is it exactly zero (a cut wire), are there jumps larger than any healthy step, and is it noisier than normal. They describe the signal. The verdict combines them with what the other sensors show."],
    cause: ["Likely cause", "A rule-based hint from the kind of signal that moved first: flow and pressure, vibration, temperature or electrical. It tells the technician where to start looking. It does not name a specific part on this rig; part-level diagnosis is on the Components page."],
    tags: ["Sensor tags", "Tags follow the convention of plant piping and instrument drawings (P&ID). The first letter is what is measured: F flow, P pressure, T temperature, V vibration, I current, E voltage. The second letter T means transmitter. 101 is the loop number of pump P-101."],
    injected: ["Injected fault", "SKAB has no recorded sensor failures, so this one was added in software to real healthy data (for example a cut wire or a loose contact). Every other Monitor scenario is real recorded data."],
    truth: ["SKAB fault label", "The period that the SKAB researchers marked as the fault when they recorded the data. It is used only to score detectors, never to train them."],
    t2q: ["Classic T²+Q chart", "Hotelling's T² plus Q (SPE) is the standard multivariate control chart used in factories. Its alarms are shown on the same data for comparison."],
    feedback: ["Operator feedback", "When the LineGuard service is running, each 'false alarm' mark on a signal widens that signal's limit by 5 % (up to 50 %), so repeated nuisance alarms stop. This public demo only saves your choice in your own browser."],
    f1: ["F1 score", "One number for detection quality from 0 to 1. It balances catching fault time (missed alarms) against alarming on healthy time (false alarms). It is SKAB's main leaderboard metric. A detector that alarms all the time would still score about 0.70 here, because faults fill about half of each test run, which is why FAR and false-alarm events are shown too."],
    far: ["FAR (false alarm rate)", "The share of healthy seconds during which the detector was alarming. Lower is better."],
    mar: ["MAR (missed alarm rate)", "The share of fault seconds during which the detector stayed silent. Lower is better."],
    caught: ["Faults caught", "How many of the 34 recorded faults raised an alarm at any point while the fault was happening."],
    delay: ["Median delay", "Seconds between the labelled start of a fault and the first alarm, middle value over all caught faults."],
    events: ["False-alarm events", "Separate alarms, across all 34 runs, that never touch a labelled fault. This is what an operator actually experiences: each one is a pointless trip to the machine."],
    realtime: ["Real-time (causal)", "Each decision uses only data up to that second, as it would on a live machine. A test in the repository checks that LineGuard never looks ahead."],
    lookahead: ["Look-ahead in the published models", "SKAB's reference notebooks give each 60-second window the time of its first second. So a flag at second t was computed with data up to about t + 58 s. We re-ran the models both ways; real-time, the best one drops from F1 0.84 to 0.77 and its delay grows from 16 s to 73 s."],
    nested: ["Nested cross-validation", "Settings were chosen on two of SKAB's three experiment families and scored on the third, rotating three times. No reported score was tuned on its own test data."],
    zscore: ["Per-sensor limits", "One fixed limit per sensor, the way most plants alarm today."],
    iforest: ["Isolation Forest", "A common general-purpose outlier detector that isolates unusual points with random trees."],
    autoenc: ["Autoencoders (Conv-AE, LSTM-AE)", "Neural networks that learn to reconstruct healthy data and alarm when reconstruction fails. Conv-AE is the top entry on the SKAB leaderboard. We trained our own copies with 3 random seeds."],
    grade: ["Condition grade", "The test-rig operators set each component to known wear levels, for example valve switching at 100 %, 90 %, 80 % or 73 % of normal. The model must name the level from sensor data alone."],
    blocked: ["Held-out predictions", "The 2205 cycles are cut into blocks of 50 consecutive cycles. Each block is predicted by a model trained without it, so neighbouring, nearly identical cycles cannot leak the answer."],
    cycle: ["Load cycle", "The hydraulic rig repeats the same 60-second load pattern. One cycle is one 60-second recording of all 17 sensors."],
    montecarlo: ["Monte-Carlo simulation", "The failure time and repair time are uncertain, so the shift is simulated 300 times with random values inside your ranges and the results are averaged."],
    buffer: ["Buffer", "Parts waiting between two stations. A full buffer keeps the next station busy for a while after the one before it stops."],
    idle: ["Idle minutes", "Minutes a station stands still because nothing arrives from upstream or its output has nowhere to go. Some idling is normal on any unbalanced line, which is why the extra idle time caused by waiting is shown separately."],
    unconfirmed: ["Why offsets and drifts are 'unconfirmed'", "A sensor that slowly drifts or jumps to a new offset looks exactly like a real change in the process on that one signal. LineGuard does not guess; it asks for a two-minute check against a local gauge."],
    watch: ["Watch (early warning)", "The health score has stayed above 0.8 of the limit for 10 seconds without crossing it. Nothing is wrong yet, but one signal is drifting toward its limit. On the 34 SKAB runs this happens during under 1 % of healthy time and came before the alarm in 4 faults."],
    workorder: ["Work order", "The job ticket a maintenance team works from: what is wrong, how urgent, the evidence, and the steps to check. LineGuard fills it in from the alert. Its status moves from Open to Acknowledged, In progress and Closed. Closing it is checked against the data: LineGuard shows whether the signals came back inside their limits."],
    priority: ["Priority", "P1: confirmed machine fault; inspect this shift. P2: unconfirmed deviation; do the two-minute check now. P3: sensor fault; fix the instrument, the machine keeps running."],
    mqtt: ["MQTT", "A lightweight messaging protocol widely used to move sensor readings from machines to software in factories."],
  };
  const pop = document.createElement("div");
  pop.className = "info-pop"; pop.hidden = true; pop.setAttribute("role", "dialog");
  document.body.append(pop);
  let popOwner = null;
  const closePop = () => { pop.hidden = true; if (popOwner) popOwner.setAttribute("aria-expanded", "false"); popOwner = null; };
  function openPop(btn) {
    const [title, text] = GLOSSARY[btn.dataset.info] || ["", ""];
    pop.innerHTML = ""; pop.append(h("b", {}, title), h("p", {}, text));
    pop.hidden = false; popOwner = btn; btn.setAttribute("aria-expanded", "true");
    const r = btn.getBoundingClientRect(), w = Math.min(320, window.innerWidth - 24);
    pop.style.width = `${w}px`;
    const left = Math.max(12, Math.min(window.innerWidth - w - 12, r.left + r.width / 2 - w / 2));
    const below = r.bottom + 8 + pop.offsetHeight < window.innerHeight;
    pop.style.left = `${left + window.scrollX}px`;
    pop.style.top = `${(below ? r.bottom + 8 : r.top - pop.offsetHeight - 8) + window.scrollY}px`;
  }
  const info = (key) => h("button", { type: "button", class: "info", "data-info": key, "aria-expanded": "false", "aria-label": `What is ${(GLOSSARY[key] || [key])[0]}?` }, "i");
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".info");
    if (btn) { e.stopPropagation(); e.preventDefault(); if (popOwner === btn) closePop(); else openPop(btn); return; }
    if (!pop.contains(e.target)) closePop();
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  window.addEventListener("scroll", () => { if (popOwner) openPop(popOwner); }, { passive: true });
  const upgradeInfo = (root = document) => $$("span[data-info]", root).forEach((sp) => sp.replaceWith(info(sp.dataset.info)));

  // ------------------------------------------------------------------ theme
  let colors = {};
  const readColors = () => {
    const cs = getComputedStyle(document.documentElement);
    for (const k of ["ink", "ink-2", "ink-3", "rule", "trace", "trace-dim", "warn", "alarm", "instrument", "unconfirmed",
      "select", "baseline", "surface", "surface-2", "ok", "warn-soft"]) colors[k] = cs.getPropertyValue(`--${k}`).trim();
  };
  const savedTheme = store.get("lg-theme", null);
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  $("#theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    store.set("lg-theme", document.documentElement.dataset.theme);
    readColors(); drawAll();
  });

  // ------------------------------------------------------------------ tabs
  const showPage = (page) => {
    $$("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.page === page)));
    $$("section.page").forEach((p) => (p.hidden = p.dataset.page !== page));
    if (page === "components") drawCycle();
    if (page === "monitor") drawAll();
  };
  $$("nav.tabs button").forEach((b) => b.addEventListener("click", () => { history.replaceState(null, "", `#${b.dataset.page}`); showPage(b.dataset.page); }));

  // ------------------------------------------------------------------ state
  const state = { manifest: null, run: null, t: 0, playing: false, speed: 20, cls: null, ranges: null, scenario: null };
  const SIG = () => state.manifest.signals;
  const sigInfo = (k) => SIG().find((x) => x.key === k);

  // ------------------------------------------------------------------ schematic
  const TAGPOS = {
    Accelerometer1RMS: [192, 36], Accelerometer2RMS: [192, 86], Voltage: [192, 136],
    "Volume Flow RateRMS": [470, 36], Pressure: [470, 86], Current: [470, 136],
    Temperature: [290, 22], Thermocouple: [372, 22],
  };
  function buildSchematic(svg, withTags) {
    svg.innerHTML = "";
    const y = withTags ? 170 : 40, hgt = 56;
    const stages = [["Cutting", 20, "upstream"], ["Machining", 250, "fed by P-101"], ["Assembly", 510, "downstream"], ["Packing", 740, "downstream"]];
    svg.append(s("line", { x1: 150, y1: y + hgt / 2, x2: 740, y2: y + hgt / 2, class: "flow" }));
    [[200, "B1"], [460, "B2"], [690, "B3"]].forEach(([x]) => {
      svg.append(s("path", { d: `M${x - 14} ${y + 14} h28 l-5 28 h-18 z`, class: "buffer" }));
    });
    stages.forEach(([name, x, sub], i) => {
      const g = s("g", { class: "stage", id: `${svg.id}-stage-${i}` });
      g.append(s("rect", { x, y, width: 130, height: hgt, rx: 3 }));
      const t1 = s("text", { x: x + 12, y: y + 24 }); t1.textContent = name;
      const t2 = s("text", { x: x + 12, y: y + 42, class: "sub" }); t2.textContent = sub;
      g.append(t1, t2); svg.append(g);
    });
    if (!withTags) return;
    // Pump loop: pump -> valve -> back, feeding Machining.
    svg.append(s("path", { d: "M331 112 V170", class: "pipe" }));
    svg.append(s("path", { d: "M361 86 H420 V58 H300 V60", class: "pipe" }));
    svg.append(s("path", { d: "M412 50 l16 16 v-16 l-16 16 z", fill: "none", stroke: colors.ink || "#333", "stroke-width": 1.5, class: "valve" }));
    const pump = s("g", { class: "pump", id: "pump" });
    pump.append(s("circle", { cx: 331, cy: 86, r: 27 }));
    pump.append(s("path", { d: "M318 104 L331 64 L344 104 Z", opacity: 0.85 }));
    svg.append(pump);
    const pl = s("text", { x: 331, y: 132, "text-anchor": "middle", class: "callout", id: "pump-label" }); pl.textContent = "P-101";
    svg.append(pl);
    for (const sig of SIG()) {
      const [cx, cy] = TAGPOS[sig.key];
      const g = s("g", { class: "tag", id: `tag-${CSS.escape(sig.key)}`, "data-key": sig.key, tabindex: 0, role: "button", "aria-label": `${sig.label} sensor ${sig.tag}` });
      const tx = cx < 331 ? 304 : 358;
      g.append(s("line", { x1: cx, y1: cy, x2: cy < 30 ? cx : tx, y2: cy < 30 ? 60 : 86 }));
      g.append(s("circle", { cx, cy, r: 17 }));
      const [a, b] = sig.tag.split(" ");
      const t1 = s("text", { x: cx, y: cy - 1 }); t1.textContent = a;
      const t2 = s("text", { x: cx, y: cy + 11, style: "font-size:9px;font-weight:500" }); t2.textContent = b;
      g.append(t1, t2);
      g.addEventListener("mouseenter", () => hoverSignal(sig.key, true));
      g.addEventListener("mouseleave", () => hoverSignal(sig.key, false));
      g.addEventListener("focus", () => hoverSignal(sig.key, true));
      g.addEventListener("blur", () => hoverSignal(sig.key, false));
      svg.append(g);
    }
    const status = s("text", { x: 620, y: 60, class: "callout", id: "line-status", style: "font-size:14px" });
    const status2 = s("text", { x: 620, y: 80, class: "callout", id: "line-status-2" });
    svg.append(status, status2);
  }
  function hoverSignal(key, on) {
    const row = $(`.trace-row[data-key="${CSS.escape(key)}"]`);
    if (row) row.classList.toggle("hover", on);
    const tag = document.getElementById(`tag-${CSS.escape(key)}`);
    if (tag) tag.classList.toggle("hover", on);
  }

  // ------------------------------------------------------------------ traces
  function buildTraces() {
    const host = $("#trace-rows"); host.innerHTML = "";
    for (const sig of SIG()) {
      const row = h("div", { class: "trace-row", "data-key": sig.key },
        h("div", { class: "trace-label" },
          h("div", { class: "name" }, sig.label),
          h("div", { class: "val" }, h("span", { class: "num" }, "–"), h("i", {}, sig.tag))),
        h("canvas", { "aria-label": `${sig.label} over time` }));
      row.addEventListener("mouseenter", () => hoverSignal(sig.key, true));
      row.addEventListener("mouseleave", () => hoverSignal(sig.key, false));
      host.append(row);
    }
  }
  function sizeCanvas(c) {
    const r = c.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    const w = Math.max(10, Math.round(r.width * d)), hh = Math.max(10, Math.round(r.height * d));
    if (c.width !== w || c.height !== hh) { c.width = w; c.height = hh; }
    const ctx = c.getContext("2d"); ctx.setTransform(d, 0, 0, d, 0, 0);
    return [ctx, r.width, r.height];
  }
  const inRanges = (ranges, i) => ranges.some(([a, b]) => i >= a && i < b);
  function prepareRun() {
    const run = state.run, n = run.n;
    // Per-sample class for every signal: 0 normal, 1 outside its limit, 2 suspected bad sensor.
    const cls = {};
    const sensorAlerts = run.alerts.filter((a) => a.verdict === "sensor");
    for (const sig of SIG()) {
      const c = new Uint8Array(n), con = run.contrib[sig.key];
      for (let i = run.baseline_end; i < n; i++) if ((con[i] ?? 0) > 1) c[i] = 1;
      for (const a of sensorAlerts) if (a.lead === sig.key) for (let i = a.start; i < a.end; i++) c[i] = 2;
      cls[sig.key] = c;
    }
    state.cls = cls;
    const ranges = {};
    for (const sig of SIG()) {
      const v = run.signals[sig.key].filter((x) => x !== null).slice().sort((a, b) => a - b);
      let lo = v[Math.floor(v.length * 0.005)], hi = v[Math.floor(v.length * 0.995)];
      if (run.injected && run.injected.sensor === sig.key) { lo = Math.min(lo, v[0]); hi = Math.max(hi, v[v.length - 1]); }
      const pad = (hi - lo) * 0.12 || 1; ranges[sig.key] = [lo - pad, hi + pad];
    }
    state.ranges = ranges;
  }
  function drawTrace(row) {
    const key = row.dataset.key, c = $("canvas", row);
    const [ctx, w, hh] = sizeCanvas(c);
    const run = state.run, n = run.n, t = Math.floor(state.t), vals = run.signals[key], cls = state.cls[key];
    const [lo, hi] = state.ranges[key];
    const X = (i) => (i / (n - 1)) * w, Y = (v) => hh - 3 - ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * (hh - 6);
    ctx.clearRect(0, 0, w, hh);
    ctx.fillStyle = colors.baseline; ctx.fillRect(0, 0, X(run.baseline_end), hh);
    if (run.injected && run.injected.sensor === key) {
      ctx.fillStyle = colors["surface-2"]; ctx.fillRect(X(run.injected.start), 0, X(run.injected.end) - X(run.injected.start), hh);
    }
    // future: faint
    ctx.lineWidth = 1; ctx.strokeStyle = colors["trace-dim"]; ctx.globalAlpha = 0.45; ctx.beginPath();
    for (let i = t; i < n; i++) { const v = vals[i]; if (v === null) continue; i === t ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v)); }
    ctx.stroke(); ctx.globalAlpha = 1;
    // past, coloured by state
    const col = [colors.trace, colors.warn, colors.instrument];
    let i = 0;
    while (i < Math.min(t + 1, n)) {
      const k = cls[i]; ctx.beginPath(); ctx.strokeStyle = col[k]; ctx.lineWidth = k ? 1.6 : 1.1;
      ctx.moveTo(X(i), Y(vals[i] ?? lo));
      let j = i + 1;
      while (j < Math.min(t + 1, n) && cls[j] === k) { ctx.lineTo(X(j), Y(vals[j] ?? lo)); j++; }
      if (j < n) ctx.lineTo(X(j), Y(vals[j] ?? lo));
      ctx.stroke(); i = j;
    }
    ctx.strokeStyle = colors.ink; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(X(t) + 0.5, 0); ctx.lineTo(X(t) + 0.5, hh); ctx.stroke();
  }
  function hatch(ctx, x, y, w, hh) {
    ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, hh); ctx.clip();
    ctx.strokeStyle = colors["ink-3"]; ctx.lineWidth = 1;
    for (let k = x - hh; k < x + w; k += 5) { ctx.beginPath(); ctx.moveTo(k, y + hh); ctx.lineTo(k + hh, y); ctx.stroke(); }
    ctx.restore();
  }
  function drawScore() {
    const c = $("#score-canvas"), [ctx, w, hh] = sizeCanvas(c), run = state.run, n = run.n, t = Math.floor(state.t);
    const top = 58, X = (i) => (i / (n - 1)) * w, max = 4, Y = (v) => top - 2 - (Math.min(v, max) / max) * (top - 6);
    ctx.clearRect(0, 0, w, hh);
    ctx.fillStyle = colors.baseline; ctx.fillRect(0, 0, X(run.baseline_end), top);
    ctx.fillStyle = colors["ink-3"]; ctx.font = "11px Barlow, sans-serif";
    if (w > 300) ctx.fillText("baseline learnt", 6, 12);
    ctx.setLineDash([4, 3]); ctx.strokeStyle = colors.ink; ctx.beginPath(); ctx.moveTo(0, Y(1)); ctx.lineTo(w, Y(1)); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillText("limit", w - 26, Y(1) - 4);
    ctx.setLineDash([1, 3]); ctx.strokeStyle = colors["ink-3"]; ctx.beginPath(); ctx.moveTo(0, Y(0.8)); ctx.lineTo(w, Y(0.8)); ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.strokeStyle = colors.trace; ctx.lineWidth = 1.2;
    let started = false;
    for (let i = run.baseline_end; i <= Math.min(t, n - 1); i++) {
      const v = run.score[i]; if (v === null) continue;
      if (!started) { ctx.moveTo(X(i), Y(v)); started = true; } else ctx.lineTo(X(i), Y(v));
    }
    ctx.stroke();
    const rows = [[run.alarms, colors.warn, "LineGuard"], [run.classic_alarms, colors["trace-dim"], "T²+Q"]];
    rows.forEach(([ranges, color], k) => {
      const yy = top + 6 + k * 11;
      ctx.fillStyle = colors["surface-2"]; ctx.fillRect(0, yy, w, 8);
      if (k === 0) { ctx.fillStyle = colors["warn-soft"]; for (const [a, b] of run.watch || []) if (a <= t) ctx.fillRect(X(a), yy, X(Math.min(b, t)) - X(a), 8); }
      ctx.fillStyle = color;
      for (const [a, b] of ranges) if (a <= t) ctx.fillRect(X(a), yy, X(Math.min(b, t)) - X(a), 8);
    });
    const yy = top + 28;
    ctx.fillStyle = colors["surface-2"]; ctx.fillRect(0, yy, w, 8);
    for (const [a, b] of run.truth) hatch(ctx, X(a), yy, X(b) - X(a), 8);
    if (run.injected) { ctx.fillStyle = colors.instrument; ctx.globalAlpha = 0.5; ctx.fillRect(X(run.injected.start), yy, X(run.injected.end) - X(run.injected.start), 8); ctx.globalAlpha = 1; }
    ctx.strokeStyle = colors.ink; ctx.beginPath(); ctx.moveTo(X(t) + 0.5, 0); ctx.lineTo(X(t) + 0.5, hh); ctx.stroke();
    const sv = run.score[t];
    $("#score-val").textContent = sv === null || sv === undefined ? "learning baseline" : `${sv >= 6 ? "above 6" : sv.toFixed(2)} (limit 1.0)`;
  }

  // ------------------------------------------------------------------ alerts
  const VERDICT = { machine: "Machine fault", sensor: "Sensor fault", unconfirmed: "Unconfirmed" };
  function activeAlert(t) {
    const run = state.run;
    const live = run.alerts.find((a) => t >= a.start && t < a.end);
    if (live) return { alert: live, live: true };
    const past = run.alerts.filter((a) => a.end <= t && t - a.end < 90).pop();
    return past ? { alert: past, live: false } : null;
  }
  const EVIDENCE_S = 30;
  function renderAlert() {
    const run = state.run, t = Math.floor(state.t), panel = $("#alert-panel");
    const found = t >= run.baseline_end ? activeAlert(t) : null;
    const watching = !found && t >= run.baseline_end && (run.watch || []).some(([a, b]) => t >= a && t < b);
    const key = found ? `${found.alert.start}-${found.live}-${t - found.alert.start >= Math.min(EVIDENCE_S, found.alert.end - found.alert.start - 1)}` : (t < run.baseline_end ? "learning" : watching ? "watch" : "ok");
    updateSchematic(t, found, watching);
    if (key === "watch" && panel.dataset.key === "watch") { updateLog(t); }
    if (panel.dataset.key === key && !["ok", "learning", "watch"].includes(key)) { updateLog(t); return; }
    panel.dataset.key = key; panel.innerHTML = "";
    if (t < run.baseline_end) {
      panel.append(h("div", { class: "state learning" }, h("span", { class: "dot" }), h("h2", {}, "Learning the healthy baseline")),
        h("p", { class: "muted" }, `The first ${run.baseline_end} seconds after start-up are the reference for every limit. ${fmtTime(run.baseline_end - t)} to go.`));
    } else if (!found && watching) {
      const now = SIG().map((sg) => [sg, run.contrib[sg.key][t] ?? 0]).sort((a, b) => b[1] - a[1]);
      panel.append(h("div", { class: "state watch" }, h("span", { class: "dot" }), h("h2", {}, "Watch"), info("watch")),
        h("p", {}, `${now[0][0].label} is approaching its limit. No alarm yet; worth a look on the next round.`), bars(now.slice(0, 4), null, null));
    } else if (!found) {
      const now = SIG().map((sg) => [sg, run.contrib[sg.key][t] ?? 0]).sort((a, b) => b[1] - a[1]);
      panel.append(h("div", { class: "state" }, h("span", { class: "dot" }), h("h2", {}, "All signals inside their limits")),
        h("p", { class: "muted small" }, "Closest to a limit right now:"), bars(now.slice(0, 4), null, null));
    } else {
      const a = found.alert, ready = t - a.start >= Math.min(EVIDENCE_S, a.end - a.start - 1);
      if (!ready) {
        panel.append(h("div", { class: "state machine" }, h("span", { class: "dot", style: `background:${colors.warn}` }), h("h2", {}, "Alarm raised")),
          h("p", {}, `${sigInfo(a.lead).label} is outside its limit. LineGuard is collecting ${EVIDENCE_S} seconds of evidence before it gives a verdict.`),
          bars(SIG().map((sg) => [sg, run.contrib[sg.key][t] ?? 0]).sort((x, y) => y[1] - x[1]).slice(0, 4), null, null));
      } else {
        panel.append(alertCard(a, found.live));
      }
    }
    updateLog(t);
  }
  function bars(list, lead, verdict) {
    const max = Math.max(3, ...list.map(([, v]) => v));
    return h("div", { class: "bars" }, list.map(([sg, v]) => h("div", { class: `bar ${v > 1 ? "over" : ""} ${sg.key === lead ? `lead ${verdict}` : ""}` },
      h("span", {}, sg.label),
      h("span", { class: "track" }, h("span", { class: "fill", style: `width:${Math.min(100, (v / max) * 100)}%` }), h("span", { class: "limit", style: `left:${(1 / max) * 100}%` })),
      h("span", { class: "num" }, v.toFixed(1)))));
  }
  function alertCard(a, live) {
    const run = state.run, lead = sigInfo(a.lead), partner = sigInfo(a.partner);
    const list = SIG().map((sg) => [sg, a.contributions[sg.key] ?? 0]).sort((x, y) => y[1] - x[1]);
    const fb = store.get("lg-feedback", {}), fbKey = `${run.id}@${a.start}`;
    const fbButtons = ["Confirm fault", "Mark false alarm"].map((label) => h("button", {
      "aria-pressed": String(fb[fbKey] === label),
      onclick: (e) => {
        const all = store.get("lg-feedback", {}); all[fbKey] = label; store.set("lg-feedback", all);
        $$("button", e.target.parentElement).forEach((b) => b.setAttribute("aria-pressed", String(b === e.target)));
        $("#fb-note").textContent = `Saved: ${label.toLowerCase()}. ${window.LINEGUARD_API ? "Sent to the LineGuard service." : "Saved in this browser only."}`;
        if (window.LINEGUARD_API) fetch(`${window.LINEGUARD_API}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run: run.id, start: a.start, label, lead: a.lead }) }).catch(() => {});
      },
    }, label));
    const c = a.checks;
    const fact = (flag, yes, no) => h("li", {}, h("span", { class: flag ? "n" : "y" }, flag ? "!" : "–"), h("span", {}, flag ? yes : no));
    return h("div", { style: "display:grid;gap:14px", class: `${a.verdict}-verdict` },
      h("div", {},
        h("div", { class: `state ${a.verdict}` }, h("span", { class: "dot" }), h("span", { class: `verdict-chip ${a.verdict}` }, VERDICT[a.verdict]), info("verdict"),
          h("span", { class: "muted small" }, `${live ? "Active" : "Cleared"}, raised at ${fmtTime(a.start)}. Confidence: ${a.confidence.toLowerCase()}.`), info("confidence")),
        h("div", { class: "alert-title" }, a.title)),
      h("div", { class: "reason" }, h("ul", {}, a.reasons.map((r) => h("li", {}, r)))),
      h("div", { class: "action" }, h("b", {}, "Recommended action. "), a.action),
      workOrderBlock(a),
      h("div", {}, h("div", { class: "section-h" }, h("span", {}, "Which signals left their limits ", info("limit")), h("small", {}, "first 30 s of the alarm")), bars(list, a.lead, a.verdict)),
      h("div", {}, h("div", { class: "section-h" }, h("span", {}, "Do the other sensors agree? ", info("expected")), h("small", {}, h("span", {}, "in σ "), info("sigma"))),
        h("div", { class: "agree" },
          h("div", {}, h("b", {}, `${a.observed[a.lead] > 0 ? "+" : ""}${a.observed[a.lead].toFixed(1)}σ`), h("span", {}, `${lead.label}, measured`)),
          h("div", {}, h("b", {}, `${a.expected[a.lead] > 0 ? "+" : ""}${a.expected[a.lead].toFixed(1)}σ`), h("span", {}, `${lead.label}, expected from the other sensors`)),
          h("div", {}, h("b", {}, `${a.observed[a.partner] > 0 ? "+" : ""}${a.observed[a.partner].toFixed(1)}σ`), h("span", {}, `${partner.label} (related sensor)`)),
          h("div", {}, h("b", {}, `${a.variability[a.lead].toFixed(1)}×`), h("span", {}, `${lead.label} variability vs healthy `, info("variability"))))),
      h("div", {}, h("div", { class: "section-h" }, h("span", {}, `Signal behaviour: ${lead.label} `, info("behaviour"))),
        h("ul", { class: "checks" }, fact(c.stuck, "Frozen: repeats the same value", "Still changing over time"), fact(c.dropout, "Reads exactly zero", "Signal present"),
          fact(c.spikes, "Jumps larger than any healthy step", "No jumps beyond healthy steps"), fact(c.noise, `Noisier than normal (${a.variability[a.lead].toFixed(1)}×)`, "Noise level normal"))),
      h("div", {}, h("div", { class: "section-h" }, h("span", {}, "Likely cause ", info("cause"))), h("p", { class: "small" }, a.cause)),

      h("div", {}, h("div", { class: "feedback" }, fbButtons), h("p", { class: "small muted", id: "fb-note", style: "margin-top:6px" }, window.LINEGUARD_API ? "Feedback is logged and adjusts this signal's limit." : "Saved in your browser only. ", window.LINEGUARD_API ? null : info("feedback"))),
      h("div", { class: "truth-note" }, h("b", {}, "Ground truth: "), a.truth));
  }
  // ------------------------------------------------------------------ work orders
  const CHECKLIST = {
    hydraulic: ["Lock out and tag out pump P-101; close the suction and discharge isolation valves.", "Check every valve on the loop is in its normal position and moves freely.", "Inspect the strainer and filter for blockage.", "Look for leaks at flanges, seals and fittings.", "Restart and confirm flow and pressure return inside their limits on LineGuard."],
    vibration: ["Lock out and tag out pump P-101.", "Check the mounting bolts and baseplate for looseness.", "Check coupling alignment and wear.", "Check suction pressure and inspect the impeller for cavitation damage.", "Restart and confirm both vibration signals return inside their limits."],
    thermal: ["Check the motor cooling fan and vents are clear.", "Check fluid level and the cooler.", "Measure motor surface temperature with an IR thermometer.", "Check bearing lubrication.", "Confirm temperatures stop changing unusually fast on LineGuard."],
    electrical: ["Measure supply voltage at the motor terminals.", "Measure current on each phase with a clamp meter.", "Inspect terminals and connections for heat discolouration.", "Check the pump is not running against an unusual load.", "Confirm current and voltage return inside their limits."],
    sensor: ["Compare the reading with a local gauge or a handheld instrument.", "Inspect the cable and connector at the transmitter and the junction box.", "Check transmitter power and its 4-20 mA signal.", "Reseat or replace the cable or transmitter.", "Confirm the signal returns inside its limit. No mechanical work is needed."],
  };
  const PRIORITY = { machine: "P1", unconfirmed: "P2", sensor: "P3" };
  const WO_FLOW = ["Open", "Acknowledged", "In progress", "Closed"];
  const woKey = (a) => `${state.run.id}@${a.start}`;
  const woStatus = (a) => (store.get("lg-wo", {})[woKey(a)] || null);
  function workOrderText(a) {
    const run = state.run, lead = sigInfo(a.lead), steps = a.verdict === "sensor" ? CHECKLIST.sensor : CHECKLIST[lead.group];
    const first = a.verdict === "unconfirmed" ? [`Two-minute check: compare ${lead.label.toLowerCase()} with a local gauge. If they agree, continue below. If not, switch to the sensor checklist.`] : [];
    return [`WORK ORDER  ${PRIORITY[a.verdict]}  ${VERDICT[a.verdict]}`, `Asset: Pump P-101 (Machining feed)   Source: ${run.title}`, `Raised: ${fmtTime(a.start)} into the run   Confidence: ${a.confidence}`, "",
      `Finding: ${a.title}`, ...a.reasons.map((r) => `  - ${r}`), `Likely cause: ${a.cause}`, "",
      "Steps:", ...[...first, ...steps].map((x, i) => `  ${i + 1}. ${x}`), "",
      "Evidence (first 30 s, 1.0 = alarm limit): " + Object.entries(a.contributions).sort((x, y) => y[1] - x[1]).slice(0, 3).map(([k, v]) => `${sigInfo(k).label} ${v.toFixed(1)}`).join(", "),
      "Generated by LineGuard. Checklists are standard templates; follow site safety rules."].join("\n");
  }
  function workOrderBlock(a) {
    const status = woStatus(a);
    const wrap = h("div", { class: "wo" });
    const render = () => {
      wrap.innerHTML = "";
      const st = woStatus(a);
      if (!st) {
        wrap.append(h("button", { class: "btn primary", onclick: () => { const all = store.get("lg-wo", {}); all[woKey(a)] = "Open"; store.set("lg-wo", all); render(); updateLog(Math.floor(state.t)); } }, "Create work order"), info("workorder"));
        return;
      }
      const lead = sigInfo(a.lead), steps = a.verdict === "sensor" ? CHECKLIST.sensor : CHECKLIST[lead.group];
      const done = store.get("lg-wo-steps", {})[woKey(a)] || [];
      const items = [...(a.verdict === "unconfirmed" ? [`Two-minute check: compare ${lead.label.toLowerCase()} with a local gauge. If they agree, continue. If not, use the sensor checklist.`] : []), ...steps];
      const fixed = a.end <= Math.floor(state.t);
      wrap.append(
        h("div", { class: "section-h" }, h("span", {}, `Work order ${PRIORITY[a.verdict]} `, info("priority")), h("small", {}, `Status: ${st}`)),
        h("div", { class: "wo-flow" }, WO_FLOW.map((s2) => h("button", { "aria-pressed": String(s2 === st), onclick: () => { const all = store.get("lg-wo", {}); all[woKey(a)] = s2; store.set("lg-wo", all); render(); updateLog(Math.floor(state.t)); } }, s2))),
        h("ol", { class: "wo-steps" }, items.map((txt, i) => h("li", {}, h("label", {}, h("input", { type: "checkbox", checked: done.includes(i) ? true : null, onchange: (e) => {
          const all = store.get("lg-wo-steps", {}); const cur = new Set(all[woKey(a)] || []); e.target.checked ? cur.add(i) : cur.delete(i); all[woKey(a)] = [...cur]; store.set("lg-wo-steps", all);
        } }), " ", txt)))),
        st === "Closed" ? h("p", { class: "small " + (fixed ? "" : "warn-text") }, fixed ? "Verified in the data: the signals are back inside their limits." : "Closed, but the signals are still outside their limits. Check the fix.") : null,
        h("div", { class: "feedback" },
          h("button", { onclick: async (e) => { try { await navigator.clipboard.writeText(workOrderText(a)); e.target.textContent = "Copied"; } catch { e.target.textContent = "Copy failed"; } } }, "Copy as text"),
          h("button", { onclick: () => { const blob = new Blob([workOrderText(a)], { type: "text/plain" }); const l = h("a", { href: URL.createObjectURL(blob), download: `work-order-${state.run.id.replace(/\W+/g, "-")}-${a.start}.txt` }); document.body.append(l); l.click(); l.remove(); } }, "Download")),
        h("p", { class: "small muted" }, "Checklists are standard templates. Work orders are saved in this browser."));
    };
    void status; render();
    return wrap;
  }
  function updateLog(t) {
    const panel = $("#alert-panel"); let log = $(".log", panel);
    const past = state.run.alerts.filter((a) => a.start <= t);
    if (!past.length) { if (log) log.remove(); return; }
    const knownCount = past.filter((a) => t - a.start >= Math.min(EVIDENCE_S, a.end - a.start - 1)).length;
    const ended = past.filter((a) => a.end <= t).length, wos = past.map(woStatus).join();
    if (!log || log.dataset.n !== `${past.length}-${knownCount}-${ended}-${wos}`) {
      if (log) log.remove();
      log = h("div", { class: "log", "data-n": `${past.length}-${knownCount}-${ended}-${wos}` }, h("div", { class: "section-h" }, "Alarm log", h("small", {}, `${past.length} so far`)),
        h("ol", {}, past.map((a) => {
          const known = t - a.start >= Math.min(EVIDENCE_S, a.end - a.start - 1);
          return h("li", {}, h("button", { onclick: () => seek(a.start + EVIDENCE_S + 1) }, h("span", {}, fmtTime(a.start)),
            h("span", {}, known ? h("span", { class: `verdict-chip ${a.verdict}` }, VERDICT[a.verdict]) : h("span", { class: "muted" }, "Collecting evidence"), " ", sigInfo(a.lead).label,
              a.end <= t ? h("span", { class: "muted small" }, `, back to normal after ${fmtTime(a.end - a.start)}`) : null,
              woStatus(a) ? h("span", { class: "wo-chip" }, woStatus(a)) : null)));
        })));
      panel.append(log);
    }
  }
  function updateSchematic(t, found, watching) {
    const run = state.run;
    for (const sg of SIG()) {
      const tag = document.getElementById(`tag-${CSS.escape(sg.key)}`); if (!tag) continue;
      const k = t >= run.baseline_end ? state.cls[sg.key][t] : 0;
      tag.classList.toggle("dev", k === 1); tag.classList.toggle("bad", k === 2);
    }
    const pump = $("#pump"), ready = found && t - found.alert.start >= Math.min(EVIDENCE_S, found.alert.end - found.alert.start - 1);
    const v = found && found.live ? (ready ? found.alert.verdict : "pending") : null;
    pump.classList.toggle("machine", v === "machine" || v === "pending"); pump.classList.toggle("unconfirmed", v === "unconfirmed"); pump.classList.toggle("sensor", v === "sensor");
    [2, 3].forEach((i) => document.getElementById(`schematic-stage-${i}`).classList.toggle("risk", v === "machine" || v === "unconfirmed"));
    const st = $("#line-status"), st2 = $("#line-status-2");
    if (t < run.baseline_end) { st.textContent = "Learning baseline"; st2.textContent = `${fmtTime(t)} of ${fmtTime(run.baseline_end)}`; }
    else if (!v && watching) { st.textContent = "Watch: P-101 drifting"; st2.textContent = "No alarm yet"; }
    else if (!v) { st.textContent = "P-101 running normally"; st2.textContent = "Line flowing"; }
    else if (v === "pending") { st.textContent = "Alarm on P-101"; st2.textContent = "Collecting evidence"; }
    else if (v === "sensor") { st.textContent = `Sensor fault: ${sigInfo(found.alert.lead).tag}`; st2.textContent = "Machine unaffected, line flowing"; }
    else if (v === "machine") { st.textContent = "Machine fault on P-101"; st2.textContent = "Assembly and Packing at risk"; }
    else { st.textContent = "Unconfirmed deviation on P-101"; st2.textContent = "Check the sensor, then the machine"; }
  }

  // ------------------------------------------------------------------ playback
  let last = 0;
  function tick(ts) {
    if (state.playing) {
      const dt = last ? (ts - last) / 1000 : 0;
      state.t = Math.min(state.run.n - 1, state.t + dt * state.speed);
      if (state.t >= state.run.n - 1) setPlaying(false);
      drawAll();
    }
    last = ts; requestAnimationFrame(tick);
  }
  function setPlaying(p) { state.playing = p; $("#play").textContent = p ? "Pause" : (state.t >= state.run.n - 1 ? "Replay" : "Play"); }
  $("#play").addEventListener("click", () => {
    if (!state.playing && state.t >= state.run.n - 1) state.t = state.run.baseline_end - 20;
    setPlaying(!state.playing);
  });
  $$("#speed button").forEach((b) => b.addEventListener("click", () => {
    state.speed = Number(b.dataset.speed); $$("#speed button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  }));
  $("#scrub").addEventListener("input", (e) => { state.t = (Number(e.target.value) / 1000) * (state.run.n - 1); drawAll(); });
  function seek(t) { state.t = Math.max(0, Math.min(state.run.n - 1, t)); drawAll(); }
  function drawAll() {
    if (!state.run || $("#page-monitor").hidden) return;
    $$(".trace-row").forEach((row) => {
      drawTrace(row);
      const t = Math.floor(state.t), key = row.dataset.key, v = state.run.signals[key][t], sg = sigInfo(key);
      const k = t >= state.run.baseline_end ? state.cls[key][t] : 0;
      row.classList.toggle("dev", k === 1); row.classList.toggle("bad", k === 2);
      $(".val .num", row).textContent = v === null ? "–" : `${Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(3)} ${sg.unit}`;
    });
    drawScore(); renderAlert();
    $("#clock").textContent = fmtTime(state.t);
    $("#scrub").value = String(Math.round((state.t / (state.run.n - 1)) * 1000));
  }

  async function loadRun(file, scenarioId) {
    if (source) { source.close(); source = null; }
    const run = await (await fetch(`data/${file}`)).json();
    state.run = run; state.scenario = scenarioId;
    prepareRun();
    $$("#scenarios button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === scenarioId)));
    if (!scenarioId) $("#run-select").value = run.id; else $("#run-select").value = "";
    $("#scenario-note").innerHTML = "";
    $("#scenario-note").append(h("b", {}, run.title + ". "), run.note);
    const first = run.alerts[0];
    state.t = first ? Math.max(run.baseline_end, first.start - 60) : run.baseline_end - 10;
    $("#alert-panel").dataset.key = "";
    setPlaying(false); drawAll();
    store.set("lg-last", { file, scenarioId });
  }

  // ------------------------------------------------------------------ live mode (served by the LineGuard API)
  let source = null;
  function startLive(runId) {
    if (source) source.close();
    setPlaying(false);
    source = new EventSource(`${window.LINEGUARD_API}/stream?run=${encodeURIComponent(runId)}&speed=${state.speed}`);
    source.addEventListener("meta", (e) => {
      const m = JSON.parse(e.data), n = m.n, empty = () => new Array(n).fill(null);
      const run = { id: `live/${runId}`, title: `Live stream of ${runId}`, note: "Readings are streamed one per tick from the LineGuard service, which runs the detector online; alerts are stored in its SQLite log.",
        n, baseline_end: 400, signals: {}, contrib: {}, score: empty(), alarms: [], truth: [], classic_alarms: [], injected: null, alerts: [] };
      for (const sg of SIG()) { run.signals[sg.key] = empty(); run.contrib[sg.key] = empty(); }
      state.run = run; state.scenario = "live"; state.cls = {}; for (const sg of SIG()) state.cls[sg.key] = new Uint8Array(n);
      state.ranges = {}; for (const sg of SIG()) state.ranges[sg.key] = [0, 1];
      $("#scenario-note").innerHTML = ""; $("#scenario-note").append(h("b", {}, run.title + ". "), run.note);
      $("#alert-panel").dataset.key = "";
    });
    source.onmessage = (e) => {
      const ev = JSON.parse(e.data), run = state.run, t = ev.t;
      for (const sg of SIG()) run.signals[sg.key][t] = ev.reading[sg.key];
      if (ev.contrib) {
        for (const sg of SIG()) { run.contrib[sg.key][t] = ev.contrib[sg.key]; state.cls[sg.key][t] = ev.quality && ev.quality[sg.key] ? 2 : ev.contrib[sg.key] > 1 ? 1 : 0; }
        run.score[t] = Math.min(6, ev.score);
        const last = run.alarms[run.alarms.length - 1];
        if (ev.state === "alarm") { if (last && last[1] === t) last[1] = t + 1; else run.alarms.push([t, t + 1]); }
      }
      if (ev.alert) { run.alerts.push(ev.alert); for (const a of run.alerts) a.end = Math.max(a.end, (run.alarms.find((r) => r[0] === a.start) || [0, a.end])[1]); }
      for (const a of run.alerts) { const r = run.alarms.find((x) => x[0] === a.start); if (r) a.end = r[1]; }
      if (t % 25 === 0 || t === 399) for (const sg of SIG()) {
        const v = run.signals[sg.key].slice(0, t + 1).filter((x) => x !== null).sort((a, b) => a - b);
        const lo = v[Math.floor(v.length * 0.005)], hi = v[Math.floor(v.length * 0.995)], pad = (hi - lo) * 0.2 || 1;
        state.ranges[sg.key] = [lo - pad, hi + pad];
      }
      state.t = t; drawAll();
    };
    source.addEventListener("end", () => source.close());
  }

  // ------------------------------------------------------------------ components page
  let hyd = null, cycleIdx = 0;
  function cycleSummary(c) {
    const worn = Object.entries(hyd.components).filter(([k, m]) => c.components[k].true !== m.healthy)
      .map(([k, m]) => `${m.label}: ${m.grades[c.components[k].true.toFixed(1)].toLowerCase()}`);
    return worn.length ? worn.join("; ") : "All components healthy";
  }
  function renderComponents() {
    const res = state.manifest.results.hydraulic;
    if (!res || !hyd) { $("#page-components .page-head p").textContent = "Hydraulic results are not available in this build."; return; }
    const k = $("#hyd-kpis"); k.innerHTML = "";
    for (const [comp, m] of Object.entries(res.components)) {
      const grades = Object.entries(m.per_grade).filter(([g]) => Number(g) !== hyd.components[comp].healthy);
      const mild = grades.sort((a, b) => Math.abs(Number(a[0]) - hyd.components[comp].healthy) - Math.abs(Number(b[0]) - hyd.components[comp].healthy))[0];
      k.append(h("div", { class: "kpi" }, h("b", {}, pct(m.accuracy, 1)), h("span", {}, `${m.label}: grade named correctly`),
        h("em", {}, `Mildest wear (${hyd.components[comp].grades[String(Number(mild[0]).toFixed(1))] || mild[0]}) caught in ${pct(mild[1].flagged_as_degraded)} of cycles`)));
    }
    const list = $("#cycle-list"); list.innerHTML = "";
    hyd.cycles.forEach((c, i) => list.append(h("button", { "aria-pressed": String(i === cycleIdx), onclick: () => { cycleIdx = i; renderComponents(); drawCycle(); } },
      h("span", { class: "c" }, `Cycle ${c.cycle}`), h("span", {}, cycleSummary(c)))));
    const cyc = hyd.cycles[cycleIdx], board = $("#condition-board"); board.innerHTML = "";
    $("#cycle-label").textContent = `cycle ${cyc.cycle}`;
    for (const [comp, meta] of Object.entries(hyd.components)) {
      const r = cyc.components[comp];
      const grades = Object.keys(meta.grades).map(Number).sort((a, b) => Math.abs(a - meta.healthy) - Math.abs(b - meta.healthy));
      const cells = grades.map((g) => {
        const cl = [g === r.pred ? "pred" : "", g === r.pred && g !== meta.healthy ? "degraded" : "", g === r.true ? "truth" : ""].join(" ");
        return h("div", { class: cl }, h("b", {}, `${g}`), meta.grades[g.toFixed(1)]);
      });
      const p = r.proba[r.pred.toFixed(1)];
      board.append(h("div", { class: "condition" },
        h("h3", {}, meta.label, h("small", {}, meta.unit)),
        h("div", { class: "scale" }, h("div", { class: "scale-cells" }, cells),
          h("div", { class: "scale-meta" }, `Predicted ${meta.grades[r.pred.toFixed(1)].toLowerCase()} (${pct(p ?? 0)} probability). ${r.pred === r.true ? "Matches the recorded condition." : "Recorded condition differs."}`))));
    }
  }
  function drawCycle() {
    if (!hyd) return;
    const c = $("#cycle-canvas"); if (!c.offsetParent) return;
    const [ctx, w, hh] = sizeCanvas(c), cyc = hyd.cycles[cycleIdx];
    ctx.clearRect(0, 0, w, hh);
    const series = [["PS1", colors.ink], ["FS1", colors.select]];
    series.forEach(([key, color], k) => {
      const v = cyc.traces[key], lo = Math.min(...v), hi = Math.max(...v);
      ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.beginPath();
      v.forEach((x, i) => { const X = (i / (v.length - 1)) * (w - 50) + 40, Y = hh - 22 - ((x - lo) / (hi - lo || 1)) * (hh - 42); i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); });
      ctx.stroke();
      ctx.fillStyle = color; ctx.font = "11px Barlow, sans-serif";
      ctx.textAlign = k ? "right" : "left";
      ctx.fillText(`${key} max ${hi.toFixed(1)}`, k ? w : 0, 12); ctx.fillText(`min ${lo.toFixed(1)}`, k ? w : 0, hh - 16);
      ctx.textAlign = "left";
    });
    ctx.fillStyle = colors["ink-3"]; ctx.fillText("0 s", 40, hh - 2); ctx.textAlign = "right"; ctx.fillText("60 s", w - 10, hh - 2); ctx.textAlign = "left";
  }

  // ------------------------------------------------------------------ line impact (simulation)
  const ASSUME = [
    ["alert_minute", "Alert arrives at (minute of shift)", 60], ["planned_stop_minutes", "Inspection stop (minutes)", 25],
    ["fail_min", "Failure after alert, earliest (min)", 90], ["fail_max", "Failure after alert, latest (min)", 240],
    ["repair_min", "Unplanned repair, shortest (min)", 90], ["repair_max", "Unplanned repair, longest (min)", 180],
    ["scrap", "Scrap while degraded (%)", 12], ["buffer", "Buffer between stages (parts)", 15],
    ["value", "Margin per good unit (₹)", 850], ["idle_cost", "Cost per idle minute downstream (₹)", 1200],
  ];
  function simulate(p, policy, rand) {
    const shift = 480, dt = 0.1, cycle = [0.9, 1.0, 0.95, 0.8];
    let down0, down1, degUntil;
    if (policy === "inspect") { down0 = p.alert_minute; down1 = p.alert_minute + p.planned_stop_minutes; degUntil = p.alert_minute; }
    else { const f = p.alert_minute + p.fail_min + rand() * (p.fail_max - p.fail_min); down0 = f; down1 = f + p.repair_min + rand() * (p.repair_max - p.repair_min); degUntil = f; }
    const buf = [[], [], []], st = cycle.map(() => ({ busy: 0, item: null, done: false })), idle = [0, 0, 0, 0];
    let good = 0, scrap = 0;
    for (let t = 0; t < shift; t += dt) {
      for (let i = 3; i >= 0; i--) {
        const S = st[i];
        if (S.done) {
          if (i === 3) { S.item ? good++ : scrap++; S.done = false; S.item = null; }
          else if (buf[i].length < p.buffer) { buf[i].push(S.item); S.done = false; S.item = null; }
          else { idle[i] += dt; continue; }
        }
        if (S.item === null) {
          if (i === 0) S.item = 1; else if (buf[i - 1].length) S.item = buf[i - 1].shift(); else { idle[i] += dt; continue; }
          if (i === 1 && t >= p.alert_minute && t < degUntil && rand() < p.scrap / 100) S.item = 0;
          S.busy = cycle[i];
        }
        if (i === 1 && t >= down0 && t < down1) { idle[i] += dt; continue; }
        S.busy -= dt; if (S.busy <= 1e-9) S.done = true;
      }
    }
    return { good, scrap, idle };
  }
  function runSimulation() {
    const p = {}; for (const [k] of ASSUME) p[k] = Number($(`#a-${k}`).value) || 0;
    let seed = 7; const rand = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
    const agg = {};
    for (const pol of ["inspect", "fail"]) {
      const N = 300; let g = 0, sc = 0; const idle = [0, 0, 0, 0];
      for (let r = 0; r < N; r++) { const o = simulate(p, pol, rand); g += o.good; sc += o.scrap; o.idle.forEach((v, i) => (idle[i] += v)); }
      agg[pol] = { good: g / N, scrap: sc / N, idle: idle.map((v) => v / N) };
    }
    const names = ["Cutting", "Machining", "Assembly", "Packing"];
    const cmp = $("#compare"); cmp.innerHTML = "";
    [["inspect", "Inspect at the alert"], ["fail", "Run until it fails"]].forEach(([k, label]) => {
      const a = agg[k];
      cmp.append(h("div", {}, h("h3", {}, label), h("dl", {},
        h("dt", {}, "Good units per shift"), h("dd", {}, a.good.toFixed(0)),
        h("dt", {}, "Scrapped parts"), h("dd", {}, a.scrap.toFixed(0)),
        h("dt", {}, "Assembly idle"), h("dd", {}, `${a.idle[2].toFixed(0)} min`),
        h("dt", {}, "Packing idle"), h("dd", {}, `${a.idle[3].toFixed(0)} min`))));
    });
    const extraIdle = (agg.fail.idle[2] + agg.fail.idle[3]) - (agg.inspect.idle[2] + agg.inspect.idle[3]);
    const value = (agg.inspect.good - agg.fail.good) * p.value + extraIdle * p.idle_cost;
    $("#protect").textContent = `₹${Math.round(value).toLocaleString("en-IN")} per alert`;
    const ib = $("#idle-bars"); ib.innerHTML = "";
    ib.append(h("div", { class: "section-h" }, h("span", {}, "Extra idle minutes if you wait ", info("idle")), h("small", {}, "per stage")));
    const diffs = names.map((n, i) => [n, Math.max(0, agg.fail.idle[i] - agg.inspect.idle[i])]);
    const mx = Math.max(1, ...diffs.map((d) => d[1]));
    diffs.forEach(([n, v]) => ib.append(h("div", { class: `bar ${v > 0 ? "over" : ""}` }, h("span", {}, n),
      h("span", { class: "track" }, h("span", { class: "fill", style: `width:${(v / mx) * 100}%` })), h("span", { class: "num" }, `${v.toFixed(0)} min`))));
    const svg = $("#impact-schematic");
    [2, 3].forEach((i) => document.getElementById(`impact-schematic-stage-${i}`).classList.toggle("risk", diffs[i][1] > 5));
    document.getElementById("impact-schematic-stage-1").classList.toggle("down", true);
    void svg;
  }
  function renderImpact() {
    buildSchematic($("#impact-schematic"), false);
    const f = $("#assumptions"); f.innerHTML = "";
    for (const [k, label, v] of ASSUME) f.append(h("label", {}, label, h("input", { type: "number", id: `a-${k}`, value: v, min: 0, step: "any", oninput: runSimulation })));
    runSimulation();
  }

  // ------------------------------------------------------------------ results page
  const infoFor = (name) => /LineGuard/.test(name) ? info("nested") : /T²/.test(name) ? info("t2q") : /Per-sensor/.test(name) ? info("zscore")
    : /Isolation/.test(name) ? info("iforest") : /AE/.test(name) ? info("autoenc") : null;
  function renderResults() {
    const r = state.manifest.results, L = r.ladder;
    const ours = L["LineGuard, balanced (nested CV)"], low = L["LineGuard, low-false-alarm (nested CV)"], t2q = L["PCA T² + Q"];
    const bestPub = r.published[0];
    $("#kpis").innerHTML = "";
    [[ours.f1.toFixed(3), "F1 on SKAB, nested cross-validation", `Best published on the SKAB leaderboard: ${bestPub.f1.toFixed(2)}`],
     [`${ours.detected}/${ours.faults}`, "faults caught", `median ${ours.median_delay_s.toFixed(0)} s after the labelled start`],
     [`${ours.false_alarm_events}`, "false-alarm events across all runs", `classic T²+Q: ${t2q.false_alarm_events}`],
     [pct(r.robustness.signature_faults_called_sensor), "stuck, dead, spiky or noisy sensors identified as sensor faults",
      `real machine faults dismissed as sensor faults: ${pct(r.robustness.machine_dismissed_as_sensor)}`]]
      .forEach(([b, sp, em]) => $("#kpis").append(h("div", { class: "kpi" }, h("b", {}, b), h("span", {}, sp), h("em", {}, em))));
    const tbl = $("#ladder"); tbl.innerHTML = "";
    tbl.append(h("thead", {}, h("tr", {}, [["Model"], ["F1", "f1"], ["FAR %", "far"], ["MAR %", "mar"], ["Faults caught", "caught"], ["Median delay", "delay"], ["False-alarm events", "events"], ["Real-time", "realtime"]].map(([x, k], i) => h("th", { class: i ? "num" : "" }, x, k ? info(k) : null)))));
    const body = h("tbody");
    const row = (name, m, causal, isOurs, sd) => body.append(h("tr", { class: isOurs ? "ours" : "" },
      h("td", {}, name, infoFor(name)), h("td", { class: "num" }, m.f1.toFixed(3) + (sd ? ` ± ${sd.f1.toFixed(3)}` : "")), h("td", { class: "num" }, m.far.toFixed(1)),
      h("td", { class: "num" }, m.mar.toFixed(1)), h("td", { class: "num" }, m.detected !== undefined ? `${m.detected}/${m.faults}` : `${Math.round(m.recall * 34)}/34`),
      h("td", { class: "num" }, m.median_delay_s === null || Number.isNaN(m.median_delay_s) ? "–" : `${Math.round(m.median_delay_s)} s`),
      h("td", { class: "num" }, typeof m.false_alarm_events === "number" ? (Number.isInteger(m.false_alarm_events) ? m.false_alarm_events : m.false_alarm_events.toFixed(1)) : "–"),
      h("td", { class: "num" }, causal ? "yes" : h("span", {}, "no, sees ~1 min ahead ", info("lookahead")))));
    for (const [name, m] of Object.entries(L)) row(name, m, true, !!m.ours);
    for (const [name, m] of Object.entries(r.deep || {})) row(`${name}, our re-implementation`, m.mean, m.causal, false, m.std);
    tbl.append(body);
    const wst = r.watch; $("#ladder-note").textContent = (wst ? `Early warning: a Watch state (health score above ${wst.level} for ${wst.seconds} s) is active during ${(wst.healthy_share * 100).toFixed(1)} % of healthy time and came before the alarm in ${wst.faults_warned_earlier} faults, a median of ${Math.round(wst.median_lead_s)} s earlier. ` : "") + "FAR and MAR are SKAB's pointwise false- and missed-alarm rates. False-alarm events count separate alarms that never touch a labelled fault. Deep models: mean ± standard deviation over 3 training seeds. 'Real-time' means each decision uses only data up to that second.";
    const v = $("#verdicts"); v.innerHTML = "";
    v.append(h("thead", {}, h("tr", {}, ["What actually happened", "Machine", "Sensor", "Unconfirmed"].map((x, i) => h("th", { class: i ? "num" : "" }, x, i === 0 ? info("verdict") : i === 3 ? info("unconfirmed") : null)))));
    const vb = h("tbody"), LABEL = { machine: "Real machine fault (SKAB)", stuck: "Injected: stuck sensor", dropout: "Injected: signal lost", spikes: "Injected: loose contact (spikes)", drift: "Injected: calibration drift", bias: "Injected: sudden offset", noise: "Injected: excess noise" };
    for (const [k, row2] of Object.entries(r.robustness.verdicts)) vb.append(h("tr", {}, h("td", {}, LABEL[k] || k), ...["machine", "sensor", "unconfirmed"].map((c) => h("td", { class: "num" }, String(row2[c])))));
    v.append(vb);
    const det = r.robustness.detection, tot = Object.values(det).reduce((a, b) => [a[0] + b.detected, a[1] + b.injected], [0, 0]);
    $("#verdict-note").textContent = `Injected sensor faults raised an alarm in ${tot[0]} of ${tot[1]} cases. Offsets and drifts look exactly like a real change on one signal, so LineGuard reports them as unconfirmed and asks for a quick gauge check rather than guessing. When it does call a sensor fault, it names the right sensor in ${pct(r.robustness.correct_sensor_named)} of cases.`;
    const pt = $("#published"); pt.innerHTML = "";
    pt.append(h("thead", {}, h("tr", {}, ["Model", "F1", "FAR %", "MAR %"].map((x, i) => h("th", { class: i ? "num" : "" }, x)))));
    pt.append(h("tbody", {}, r.published.map((p) => h("tr", {}, h("td", {}, p.model), h("td", { class: "num" }, p.f1.toFixed(2)), h("td", { class: "num" }, p.far.toFixed(2)), h("td", { class: "num" }, p.mar.toFixed(2))))));
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    readColors();
    const manifest = await (await fetch("data/manifest.json")).json();
    state.manifest = manifest;
    buildSchematic($("#schematic"), true); buildTraces();
    const sc = $("#scenarios");
    for (const x of manifest.scenarios) sc.append(h("button", { "data-id": x.id, "aria-pressed": "false", onclick: () => loadRun(x.file, x.id) },
      x.title, x.tag === "injected" ? h("span", { class: "tagnote" }, "injected") : null));
    if (manifest.scenarios.some((x) => x.tag === "injected")) sc.append(h("span", { class: "small muted", style: "align-self:center" }, "What does injected mean?", info("injected")));
    const sel = $("#run-select");
    for (const r of manifest.runs) sel.append(h("option", { value: r.file }, r.id));
    sel.addEventListener("change", () => sel.value && loadRun(sel.value, null));
    if (window.LINEGUARD_API) {
      $("#mode-note").textContent = "Connected to the LineGuard service";
      sc.append(h("button", { "data-id": "live", "aria-pressed": "false", onclick: () => {
        const chosen = manifest.runs.find((r) => r.file === sel.value);
        startLive(chosen ? chosen.id : "valve1/3");
        $$("#scenarios button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === "live")));
      } }, "Live stream", h("span", { class: "tagnote" }, "API")));
    }
    try { hyd = await (await fetch("data/hydraulic.json")).json(); } catch { hyd = null; }
    renderComponents(); renderImpact(); renderResults(); upgradeInfo();
    const lastRun = store.get("lg-last", null);
    const firstMachine = manifest.scenarios.find((x) => x.id === "machine") || manifest.scenarios[0];
    await loadRun(lastRun?.file || firstMachine.file, lastRun ? lastRun.scenarioId : firstMachine.id).catch(() => loadRun(firstMachine.file, firstMachine.id));
    const page = location.hash.slice(1); if (page && $(`section[data-page="${page}"]`)) showPage(page);
    window.addEventListener("resize", () => { drawAll(); drawCycle(); });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); drawAll(); });
    requestAnimationFrame(tick);
  }
  boot().catch((e) => { console.error(e); $("#scenario-note").textContent = `Could not load the replay data (${e.message}). Run "python -m lineguard export" and serve the web folder.`; });
})();
