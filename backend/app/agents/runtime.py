"""Run context: explicit state machine, task tree, event log, and budgets."""
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

STATES = ("IDLE", "INVESTIGATING", "PLANNING", "VALIDATING", "REJECTED", "RETRYING",
          "SIMULATING", "VERIFIED", "ESCALATED")

# Allowed transitions; anything else is a programming error.
TRANSITIONS: Dict[str, Set[str]] = {
    "IDLE": {"INVESTIGATING"},
    "INVESTIGATING": {"PLANNING", "VERIFIED", "ESCALATED"},
    "PLANNING": {"VALIDATING", "REJECTED", "ESCALATED"},
    "VALIDATING": {"REJECTED", "SIMULATING"},
    "REJECTED": {"RETRYING", "ESCALATED"},
    "RETRYING": {"PLANNING", "ESCALATED"},
    "SIMULATING": {"VERIFIED", "REJECTED"},
    "VERIFIED": set(),
    "ESCALATED": set(),
}


class InvalidTransition(RuntimeError):
    pass


@dataclass
class RunContext:
    run_id: str
    fingerprint: str
    pacing_s: float = 0.0
    on_event: Optional[Callable[[dict], None]] = None
    state: str = "IDLE"
    events: List[dict] = field(default_factory=list)
    tasks: List[dict] = field(default_factory=list)
    transitions: List[dict] = field(default_factory=list)
    attempts: List[dict] = field(default_factory=list)
    learned: Set[str] = field(default_factory=set)
    tabu: Set[str] = field(default_factory=set)
    exhausted: Set[str] = field(default_factory=set)
    llm_calls: int = 0
    escalation_reason: Optional[str] = None
    started: float = field(default_factory=time.perf_counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def emit(self, kind: str, agent: str, title: str, detail: str = "", data: Optional[dict] = None,
             task_id: Optional[str] = None) -> dict:
        with self._lock:
            event = {"seq": len(self.events) + 1, "t_ms": self.elapsed_ms(), "kind": kind, "agent": agent,
                     "state": self.state, "title": title, "detail": detail, "data": data or {},
                     "task_id": task_id}
            self.events.append(event)
        if self.on_event:
            self.on_event(event)
        return event

    def transition(self, new_state: str, reason: str) -> None:
        if new_state not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} -> {new_state}")
        self.transitions.append({"from": self.state, "to": new_state, "reason": reason, "t_ms": self.elapsed_ms()})
        self.state = new_state
        self.emit("state", "coordinator", f"→ {new_state}", reason)
        self.pace()

    def add_task(self, agent: str, title: str, parent: Optional[str], depth: int) -> str:
        task_id = f"t{len(self.tasks) + 1}"
        task = {"id": task_id, "agent": agent, "title": title, "parent": parent,
                "depth": depth, "status": "running", "result": None}
        self.tasks.append(task)
        self.emit("task", agent, f"spawn {agent} task", title, dict(task), task_id)
        return task_id

    def finish_task(self, task_id: str, status: str, result: str = "") -> None:
        for task in self.tasks:
            if task["id"] == task_id:
                task["status"] = status
                task["result"] = result

    def pace(self) -> None:
        if self.pacing_s > 0:
            time.sleep(self.pacing_s)
