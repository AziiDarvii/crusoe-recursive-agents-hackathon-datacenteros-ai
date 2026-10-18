"""API tests: inject cooling failure -> background recursive run -> committed recovery."""
import time

import pytest
from fastapi.testclient import TestClient

from app.agents.llm import MockProvider
from app.config import AgentConfig
from app.main import create_app
from app.memory import MemoryStore
from app.service import DataCenterService


@pytest.fixture()
def client():
    service = DataCenterService(MockProvider(), MemoryStore(":memory:"), AgentConfig(pacing_s=0))
    return TestClient(create_app(service))


def wait_for(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_state_is_labelled_simulated(client):
    body = client.get("/api/state").json()
    assert body["simulated"] is True and len(body["racks"]) == 8
    assert sum(r["gpu_count"] for r in body["racks"]) == 64


def test_inject_cooling_failure_runs_agents_and_commits(client):
    resp = client.post("/api/inject/cooling-failure", json={})
    assert resp.status_code == 200
    run = wait_for(client, resp.json()["run_id"])
    assert run["status"] == "finished" and run["result"]["status"] == "VERIFIED"
    state = client.get("/api/state").json()
    assert state["violations"] == []
    assert client.get("/api/memory").json()["attempts"]
    assert client.get("/api/runs").json()["runs"][0]["status"] == "VERIFIED"


def test_input_validation(client):
    assert client.post("/api/inject/cooling-failure", json={"rack_id": "rack-99"}).status_code == 422
    assert client.post("/api/inject/cooling-failure", json={"loss": 2}).status_code == 422
    assert client.post("/api/reset", json={"scenario": "bogus"}).status_code == 422
    assert client.get("/api/runs/missing").status_code == 404


def test_reset_and_manual_run_escalates_infeasible(client):
    assert client.post("/api/reset", json={"scenario": "infeasible"}).json()["violations"]
    run = wait_for(client, client.post("/api/runs", json={}).json()["run_id"])
    assert run["result"]["status"] == "ESCALATED"


def test_config_roundtrip_and_provider(client):
    cfg = client.get("/api/config").json()
    cfg["max_attempts"] = 2
    assert client.put("/api/config", json=cfg).json()["max_attempts"] == 2
    assert client.put("/api/config", json={**cfg, "max_attempts": 99}).status_code == 422
    assert client.get("/api/provider").json()["mock"] is True


def test_benchmarks_endpoint(client):
    rows = client.post("/api/benchmarks").json()["rows"]
    assert len(rows) == 5 and all(r["as_expected"] for r in rows)
