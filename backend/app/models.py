"""Immutable domain model for the simulated data center."""
from dataclasses import asdict, dataclass, replace
from typing import Optional, Tuple


@dataclass(frozen=True)
class Rack:
    id: str
    name: str
    gpu_model: str
    gpu_count: int
    gpu_memory_gb: int
    power_limit_kw: float
    cooling_capacity_kw: float
    supply_air_c: float
    cooling_derate: float = 1.0   # 1.0 nominal, 0.7 after a 30% cooling loss
    unavailable_gpus: int = 0
    power_cap: float = 1.0        # simulated GPU power cap fraction

    @property
    def effective_cooling_kw(self) -> float:
        return self.cooling_capacity_kw * self.cooling_derate

    @property
    def usable_gpus(self) -> int:
        return self.gpu_count - self.unavailable_gpus


@dataclass(frozen=True)
class Job:
    id: str
    name: str
    kind: str                     # "training" | "inference"
    priority: int                 # 1 critical, 2 high, 3 normal, 4 low
    gpus: int
    memory_gb_per_gpu: int
    utilization: float            # 0..1 GPU load while running
    remaining_h: float            # remaining runtime at full throughput
    deadline_h: float             # hours from now
    rack_id: Optional[str]        # None = queued, not placed
    start_h: float = 0.0
    delay_h: float = 0.0          # non-productive restore time
    migratable: bool = True
    deferrable: bool = False


@dataclass(frozen=True)
class Facility:
    power_limit_kw: float
    horizon_h: float = 6.0
    step_h: float = 0.25


@dataclass(frozen=True)
class DataCenterState:
    scenario: str
    racks: Tuple[Rack, ...]
    jobs: Tuple[Job, ...]
    facility: Facility
    incidents: Tuple[str, ...] = ()

    def rack(self, rack_id: str) -> Optional[Rack]:
        return next((r for r in self.racks if r.id == rack_id), None)

    def job(self, job_id: str) -> Optional[Job]:
        return next((j for j in self.jobs if j.id == job_id), None)

    def with_rack(self, rack: Rack) -> "DataCenterState":
        return replace(self, racks=tuple(rack if r.id == rack.id else r for r in self.racks))

    def with_job(self, job: Job) -> "DataCenterState":
        return replace(self, jobs=tuple(job if j.id == job.id else j for j in self.jobs))

    def with_incident(self, text: str) -> "DataCenterState":
        return replace(self, incidents=self.incidents + (text,))

    def to_dict(self) -> dict:
        return asdict(self)


ACTION_KINDS = ("migrate", "defer", "power_cap", "schedule")


@dataclass(frozen=True)
class Action:
    kind: str                     # one of ACTION_KINDS
    target: str                   # job id (migrate/defer/schedule) or rack id (power_cap)
    rack_id: Optional[str] = None
    start_h: Optional[float] = None
    cap: Optional[float] = None

    def signature(self) -> str:
        return f"{self.kind}:{self.target}:{self.rack_id}:{self.start_h}:{self.cap}"

    def describe(self) -> str:
        if self.kind == "migrate":
            return f"Migrate {self.target} -> {self.rack_id}"
        start = self.start_h or 0.0
        if self.kind == "defer":
            where = f" on {self.rack_id}" if self.rack_id else ""
            return f"Defer {self.target} to t+{start:g}h{where}"
        if self.kind == "schedule":
            return f"Schedule {self.target} on {self.rack_id} at t+{start:g}h"
        if self.kind == "power_cap" and self.cap is not None:
            return f"Cap {self.target} GPU power at {self.cap:.0%}"
        return f"{self.kind} {self.target}"


@dataclass(frozen=True)
class Plan:
    actions: Tuple[Action, ...]
    strategy: str
    author: str
    rationale: str

    def signature(self) -> str:
        return "|".join(sorted(a.signature() for a in self.actions)) or "noop"

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "author": self.author,
            "rationale": self.rationale,
            "signature": self.signature(),
            "actions": [{**asdict(a), "description": a.describe()} for a in self.actions],
        }
