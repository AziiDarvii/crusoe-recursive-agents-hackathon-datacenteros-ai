"""Application service: owns the live digital twin, config, memory and background agent runs."""
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Dict, List, Optional

from . import scenarios
from .agents.coordinator import Coordinator
from .agents.llm import CallStats, LLMProvider
from .agents.specialists import AgentServices
from .config import AgentConfig
from .memory import MemoryStore
from .metrics import compute_metrics, rack_snapshot
from .models import DataCenterState
from .safety import find_violations
from .simulator import simulate

log = logging.getLogger("datacenteros.service")


class RunInProgress(RuntimeError):
    pass


@dataclass
class LiveRun:
    run_id: str
    events: List[dict] = field(default_factory=list)
    status: str = "running"
    result: Optional[dict] = None
    error: Optional[str] = None


class DataCenterService:
    def __init__(self, provider: LLMProvider, memory: MemoryStore, config: Optional[AgentConfig] = None) -> None:
        self.provider = provider
        self.memory = memory
        self.config = config or AgentConfig()
        self._lock = threading.Lock()
        self._state = scenarios.normal()
        self._pre_incident = self._state
        self._runs: Dict[str, LiveRun] = {}
        self._active: Optional[str] = None

    # -------------------------------------------------------------- twin
    @property
    def state(self) -> DataCenterState:
        return self._state

    def reset(self, scenario: str) -> DataCenterState:
        self._ensure_idle()
        if scenario not in scenarios.SCENARIOS:
            raise ValueError(f"unknown scenario {scenario}")
        self._state = scenarios.SCENARIOS[scenario]()
        self._pre_incident = scenarios.normal()
        return self._state

    def inject_cooling_failure(self, rack_id: str, loss: float) -> DataCenterState:
        self._ensure_idle()
        self._pre_incident = self._state
        self._state = replace(scenarios.inject_cooling_failure(self._state, rack_id, loss), scenario="cooling_failure")
        return self._state

    def snapshot(self) -> dict:
        state = self._state
        sim = simulate(state)
        jobs = []
        for job in state.jobs:
            outcome = sim.jobs[job.id]
            status = "queued" if job.rack_id is None else ("deferred" if job.start_h > 0 else "running")
            jobs.append({**asdict(job), "status": status, "completion_h": outcome.completion_h,
                         "missed_deadline": outcome.missed_deadline, "lateness_h": round(outcome.lateness_h, 2)})
        return {
            "simulated": True, "scenario": state.scenario, "incidents": list(state.incidents),
            "facility": asdict(state.facility), "racks": rack_snapshot(state, sim), "jobs": jobs,
            "metrics": compute_metrics(state, sim), "pre_incident_metrics": compute_metrics(self._pre_incident),
            "violations": [v.to_dict() for v in find_violations(state, sim)],
            "facility_series": [round(x, 2) for x in sim.facility_kw], "times": list(sim.times),
            "active_run": self._active,
        }

    # -------------------------------------------------------------- runs
    def _ensure_idle(self) -> None:
        if self._active is not None:
            raise RunInProgress(f"run {self._active} is still in progress")

    def start_run(self, objective: Optional[str] = None) -> str:
        with self._lock:
            self._ensure_idle()
            run_id = uuid.uuid4().hex[:10]
            live = LiveRun(run_id)
            self._runs[run_id] = live
            self._active = run_id
        state = self._state
        thread = threading.Thread(target=self._execute, args=(live, state, objective), daemon=True)
        thread.start()
        return run_id

    def _execute(self, live: LiveRun, state: DataCenterState, objective: Optional[str]) -> None:
        svc = AgentServices(self.provider, self.memory, self.config, CallStats())
        try:
            kwargs = {"objective": objective} if objective else {}
            result, final_state = Coordinator(svc).run(state, run_id=live.run_id,
                                                       on_event=live.events.append, **kwargs)
            if result["status"] == "VERIFIED" and result["plan"]:
                self._state = final_state
            live.result = result
            live.status = "finished"
        except Exception as exc:  # surface any failure to the UI instead of hanging the run
            log.exception("agent run %s failed", live.run_id)
            live.error = f"{type(exc).__name__}: {exc}"
            live.status = "error"
        finally:
            self._active = None

    def run_status(self, run_id: str) -> Optional[dict]:
        live = self._runs.get(run_id)
        if live is not None:
            return {"run_id": run_id, "status": live.status, "events": list(live.events),
                    "result": live.result, "error": live.error, "replay": False}
        stored = self.memory.get_run(run_id)
        if stored is None:
            return None
        return {"run_id": run_id, "status": "finished", "events": stored["events"], "result": stored["result"],
                "error": None, "replay": True}

    def update_config(self, config: AgentConfig) -> AgentConfig:
        self._ensure_idle()
        self.config = config
        return config
