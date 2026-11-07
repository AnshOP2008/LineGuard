"""API smoke tests (FastAPI TestClient)."""

import pytest

from lineguard.config import SKAB_DIR

pytestmark = pytest.mark.skipif(not any(SKAB_DIR.glob("**/valve1")), reason="SKAB not downloaded")


def test_api(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEGUARD_DB", str(tmp_path / "t.db"))
    import importlib

    import service.app as svc
    importlib.reload(svc)
    from fastapi.testclient import TestClient

    client = TestClient(svc.app)
    assert client.get("/api/health").json()["status"] == "ok"
    assert "valve1/3" in client.get("/api/runs").json()
    r = client.post("/api/feedback", json={"run": "valve1/3", "start": 600, "label": "Mark false alarm", "lead": "Pressure"})
    assert r.json()["limit_multipliers"]["Pressure"] == pytest.approx(1.05)
    assert "LINEGUARD_API" in client.get("/").text
