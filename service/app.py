"""LineGuard service: live monitoring API + the dashboard.

    python -m lineguard serve        # http://localhost:8000

Endpoints
  GET  /api/health                     service status
  GET  /api/runs                       SKAB runs available for live replay
  GET  /api/stream?run=valve1/3&speed=20   Server-Sent Events: one message per
                                       reading with the online detector's output;
                                       alerts are also written to SQLite
  POST /api/feedback                   operator verdict on an alert
  GET  /api/alerts                     recent alerts with feedback
  GET  /api/limits                     current per-signal limit multipliers

Readings can also arrive over MQTT (see service/mqtt_bridge.py); the same
OnlineMonitor processes them.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lineguard.config import ARTIFACTS_DIR, ROOT, SIGNALS, DetectorConfig
from lineguard.data import load_anomaly_free, load_runs
from lineguard.online import OnlineMonitor
from lineguard.relationships import RelationshipModel, normal_training_frames

DB_PATH = Path(os.environ.get("LINEGUARD_DB", ARTIFACTS_DIR / "lineguard.db"))
WEB = ROOT / "web"
# Each confirmed false alarm on a signal widens its limit by 5 %, capped at +50 %.
FEEDBACK_STEP, FEEDBACK_CAP = 0.05, 1.5

app = FastAPI(title="LineGuard", version="1.0")
_cache: dict = {}


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, created REAL, run TEXT, start INTEGER,
                   verdict TEXT, lead TEXT, title TEXT, payload TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, created REAL, run TEXT, start INTEGER,
                   label TEXT, lead TEXT)""")
    return con


def runs() -> dict:
    if "runs" not in _cache:
        _cache["runs"] = {r.name: r for r in load_runs()}
    return _cache["runs"]


def relations() -> RelationshipModel:
    if "relations" not in _cache:
        _cache["relations"] = RelationshipModel().fit(normal_training_frames(list(runs().values()), load_anomaly_free()))
    return _cache["relations"]


def shipped_config() -> DetectorConfig:
    path = ARTIFACTS_DIR / "shipped_configs.json"
    if path.exists():
        return DetectorConfig(**json.loads(path.read_text())["balanced"])
    return DetectorConfig()


def limit_multipliers() -> dict[str, float]:
    with closing(db()) as con:
        rows = con.execute("SELECT lead, COUNT(*) FROM feedback WHERE label = 'Mark false alarm' GROUP BY lead").fetchall()
    return {lead: min(FEEDBACK_CAP, 1 + FEEDBACK_STEP * n) for lead, n in rows if lead}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "signals": SIGNALS, "config": shipped_config().__dict__}


@app.get("/api/runs")
def list_runs() -> list[str]:
    return list(runs())


@app.get("/api/stream")
async def stream(run: str, speed: float = 20.0):
    if run not in runs():
        raise HTTPException(404, f"Unknown run {run}")
    frame = runs()[run].frame
    monitor = OnlineMonitor(shipped_config(), relations(), limit_multipliers())

    async def events():
        yield f"event: meta\ndata: {json.dumps({'run': run, 'n': len(frame), 'signals': SIGNALS})}\n\n"
        for _, row in frame.iterrows():
            out = monitor.push({s: float(row[s]) for s in SIGNALS})
            if "alert" in out:
                a = out["alert"]
                with closing(db()) as con, con:
                    con.execute("INSERT INTO alerts (created, run, start, verdict, lead, title, payload) VALUES (?,?,?,?,?,?,?)",
                                (time.time(), run, a["start"], a["verdict"], a["lead"], a["title"], json.dumps(a)))
            yield f"data: {json.dumps(out, default=float)}\n\n"
            await asyncio.sleep(1.0 / max(speed, 0.1))
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


class Feedback(BaseModel):
    run: str
    start: int
    label: str
    lead: str | None = None


@app.post("/api/feedback")
def feedback(item: Feedback) -> dict:
    with closing(db()) as con, con:
        con.execute("INSERT INTO feedback (created, run, start, label, lead) VALUES (?,?,?,?,?)",
                    (time.time(), item.run, item.start, item.label, item.lead))
    return {"saved": True, "limit_multipliers": limit_multipliers()}


@app.get("/api/alerts")
def alerts(limit: int = 50) -> list[dict]:
    with closing(db()) as con:
        rows = con.execute("SELECT created, run, start, verdict, lead, title FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(["created", "run", "start", "verdict", "lead", "title"], r)) for r in rows]


@app.get("/api/limits")
def limits() -> dict:
    return limit_multipliers()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return html.replace("<script src=\"app.js\"></script>", "<script>window.LINEGUARD_API='/api'</script><script src=\"app.js\"></script>")


app.mount("/", StaticFiles(directory=WEB), name="web")
