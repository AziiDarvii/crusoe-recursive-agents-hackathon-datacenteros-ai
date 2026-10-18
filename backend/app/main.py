"""FastAPI entry point for DataCenterOS AI (simulation only — never touches real infrastructure)."""
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config as C
from .agents.llm import provider_from_env
from .agents.prompts import TOOL_PERMISSIONS
from .benchmark import run_benchmarks
from .config import AgentConfig, ObjectiveWeights
from .memory import MemoryStore
from .scenarios import SCENARIOS
from .service import DataCenterService, RunInProgress

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "datacenteros.db"
ScenarioName = Literal["normal", "cooling_failure", "power_constraint", "deadline", "infeasible"]


class ResetRequest(BaseModel):
    scenario: ScenarioName = "normal"


class InjectRequest(BaseModel):
    rack_id: str = Field("rack-04", pattern=r"^rack-0[1-8]$")
    loss: float = Field(0.30, gt=0.0, lt=0.95)
    auto_recover: bool = True


class RunRequest(BaseModel):
    objective: Optional[str] = Field(None, max_length=300)


class WeightsModel(BaseModel):
    energy: float = Field(ObjectiveWeights.energy, ge=0, le=100)
    thermal: float = Field(ObjectiveWeights.thermal, ge=0, le=100)
    utilization: float = Field(ObjectiveWeights.utilization, ge=0, le=100)
    deadline: float = Field(ObjectiveWeights.deadline, ge=0, le=100)
    migration: float = Field(ObjectiveWeights.migration, ge=0, le=100)


class ConfigModel(BaseModel):
    max_attempts: int = Field(C.DEFAULT_MAX_ATTEMPTS, ge=1, le=8)
    max_depth: int = Field(C.DEFAULT_MAX_DEPTH, ge=1, le=8)
    max_llm_calls: int = Field(C.DEFAULT_MAX_LLM_CALLS, ge=0, le=40)
    pacing_s: float = Field(C.DEFAULT_PACING_S, ge=0, le=2)
    use_cross_run_memory: bool = False
    weights: WeightsModel = WeightsModel()


def create_app(service: Optional[DataCenterService] = None) -> FastAPI:
    app = FastAPI(title="DataCenterOS AI", version="0.1.0",
                  description="Recursive agents operating a SIMULATED GPU data center digital twin.")
    origins = os.environ.get("DCOS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    app.state.service = service or DataCenterService(
        provider_from_env(), MemoryStore(os.environ.get("DCOS_DB_PATH", str(DEFAULT_DB))))

    def svc(request: Request) -> DataCenterService:
        return request.app.state.service

    def guarded(fn):
        try:
            return fn()
        except RunInProgress as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "simulation_only": True}

    @app.get("/api/state")
    def get_state(s: DataCenterService = Depends(svc)) -> dict:
        return s.snapshot()

    @app.get("/api/scenarios")
    def list_scenarios() -> dict:
        return {"scenarios": list(SCENARIOS)}

    @app.post("/api/reset")
    def reset(body: ResetRequest, s: DataCenterService = Depends(svc)) -> dict:
        guarded(lambda: s.reset(body.scenario))
        return s.snapshot()

    @app.post("/api/inject/cooling-failure")
    def inject(body: InjectRequest, s: DataCenterService = Depends(svc)) -> dict:
        guarded(lambda: s.inject_cooling_failure(body.rack_id, body.loss))
        run_id = guarded(s.start_run) if body.auto_recover else None
        return {"run_id": run_id, "state": s.snapshot()}

    @app.post("/api/runs")
    def start_run(body: RunRequest, s: DataCenterService = Depends(svc)) -> dict:
        return {"run_id": guarded(lambda: s.start_run(body.objective))}

    @app.get("/api/runs")
    def list_runs(s: DataCenterService = Depends(svc)) -> dict:
        return {"runs": s.memory.list_runs()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, s: DataCenterService = Depends(svc)) -> dict:
        status = s.run_status(run_id)
        if status is None:
            raise HTTPException(404, f"run {run_id} not found")
        return status

    @app.get("/api/memory")
    def memory(s: DataCenterService = Depends(svc)) -> dict:
        return {"attempts": s.memory.list_attempts()}

    @app.delete("/api/memory")
    def clear_memory(s: DataCenterService = Depends(svc)) -> dict:
        guarded(s._ensure_idle)
        s.memory.clear()
        return {"cleared": True}

    @app.get("/api/provider")
    def provider(s: DataCenterService = Depends(svc)) -> dict:
        p = s.provider
        return {"name": p.name, "model": p.model, "mock": p.is_mock,
                "base_url": None if p.is_mock else C.CRUSOE_BASE_URL,
                "how_to_enable": "export CRUSOE_API_KEY=... (optionally CRUSOE_MODEL) and restart the backend"}

    @app.get("/api/config")
    def get_config(s: DataCenterService = Depends(svc)) -> dict:
        return asdict(s.config)

    @app.put("/api/config")
    def put_config(body: ConfigModel, s: DataCenterService = Depends(svc)) -> dict:
        cfg = AgentConfig(body.max_attempts, body.max_depth, body.max_llm_calls, body.pacing_s,
                          body.use_cross_run_memory, ObjectiveWeights(**body.weights.model_dump()))
        return asdict(guarded(lambda: s.update_config(cfg)))

    @app.post("/api/benchmarks")
    def benchmarks(s: DataCenterService = Depends(svc)) -> dict:
        return {"rows": run_benchmarks(s.provider, s.config)}

    @app.get("/api/assumptions")
    def assumptions() -> dict:
        names = [n for n in dir(C) if n.isupper() and not n.startswith("CRUSOE")]
        return {"constants": {n: getattr(C, n) for n in names}, "tool_permissions":
                {k: sorted(v) for k, v in TOOL_PERMISSIONS.items()}}

    return app


app = create_app()
