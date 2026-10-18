"""Synthetic, clearly-simulated scenarios. Values are plausible, not real Crusoe data."""
from dataclasses import replace
from typing import Callable, Dict

from .models import DataCenterState, Facility, Job, Rack

FACILITY_POWER_LIMIT_KW = 100.0
COOLING_FAILURE_RACK = "rack-04"
COOLING_LOSS_FRACTION = 0.30


def _racks() -> tuple:
    def rack(n: int, model: str, mem: int, limit: float, cool: float, supply: float) -> Rack:
        return Rack(f"rack-{n:02d}", f"Rack {n:02d}", model, 8, mem, limit, cool, supply)

    return (
        rack(1, "H100-SXM", 80, 9.5, 10.0, 22.0),
        rack(2, "H100-SXM", 80, 9.5, 10.0, 22.2),
        rack(3, "H200-SXM", 141, 10.5, 11.0, 22.5),
        rack(4, "H200-SXM", 141, 10.5, 9.5, 22.4),
        rack(5, "H100-SXM", 80, 9.0, 9.5, 22.8),
        rack(6, "H200-SXM", 141, 10.5, 11.0, 23.0),
        rack(7, "H100-PCIe (legacy PDU)", 80, 6.0, 9.0, 21.8),
        rack(8, "H100-SXM", 80, 9.5, 10.0, 23.2),
    )


def _jobs() -> tuple:
    def job(n, name, kind, prio, gpus, mem, util, rem, dl, rack, migratable=True, deferrable=False):
        return Job(f"job-{n:02d}", name, kind, prio, gpus, mem, util, rem, dl,
                   None if rack is None else f"rack-{rack:02d}",
                   migratable=migratable, deferrable=deferrable)

    return (
        job(1, "llama-70b-finetune", "training", 2, 4, 70, 0.90, 3.0, 8.0, 1, deferrable=True),
        job(2, "chat-inference-a", "inference", 1, 2, 40, 0.55, 6.0, 6.5, 1),
        job(3, "vision-pretrain", "training", 2, 8, 72, 0.92, 5.0, 12.0, 2),
        job(4, "recsys-train", "training", 3, 4, 110, 0.85, 2.5, 10.0, 3, deferrable=True),
        job(5, "embedding-inference", "inference", 2, 2, 30, 0.50, 6.0, 6.5, 3),
        job(6, "nemotron-sft", "training", 1, 4, 120, 0.98, 4.0, 7.0, 4),
        job(7, "reward-model-train", "training", 3, 4, 64, 0.95, 3.0, 12.0, 4, deferrable=True),
        job(8, "code-llm-eval", "training", 3, 4, 50, 0.80, 1.5, 6.0, 5),
        job(9, "batch-inference", "inference", 4, 2, 24, 0.70, 4.0, 20.0, 5, deferrable=True),
        job(10, "asr-train", "training", 3, 4, 90, 0.80, 5.0, 18.0, 6, deferrable=True),
        job(11, "api-inference-b", "inference", 1, 2, 40, 0.60, 6.0, 6.5, 7),
        job(12, "diffusion-train", "training", 2, 4, 78, 0.90, 3.5, 9.0, 8),
        job(13, "safety-eval", "training", 3, 2, 40, 0.75, 1.0, 4.0, 8, deferrable=True),
        job(14, "dedup-embed", "inference", 4, 2, 20, 0.60, 2.0, 24.0, None, deferrable=True),
        job(15, "hparam-sweep", "training", 4, 4, 60, 0.85, 3.0, 30.0, None, deferrable=True),
    )


def normal() -> DataCenterState:
    return DataCenterState("normal", _racks(), _jobs(), Facility(FACILITY_POWER_LIMIT_KW))


def inject_cooling_failure(state: DataCenterState, rack_id: str = COOLING_FAILURE_RACK,
                           loss: float = COOLING_LOSS_FRACTION) -> DataCenterState:
    rack = state.rack(rack_id)
    if rack is None:
        raise ValueError(f"unknown rack {rack_id}")
    if not 0.0 < loss < 1.0:
        raise ValueError("loss must be between 0 and 1")
    failed = replace(rack, cooling_derate=round(rack.cooling_derate * (1.0 - loss), 4))
    return state.with_rack(failed).with_incident(
        f"Cooling failure: {rack_id} CRAH capacity reduced by {loss:.0%}")


def cooling_failure() -> DataCenterState:
    return replace(inject_cooling_failure(normal()), scenario="cooling_failure")


def power_constraint() -> DataCenterState:
    base = normal()
    rack = base.rack("rack-02")
    derated = replace(rack, power_limit_kw=7.0)
    return replace(base.with_rack(derated), scenario="power_constraint",
                   incidents=("PDU feed maintenance: rack-02 power budget reduced to 7.0 kW",))


def deadline() -> DataCenterState:
    urgent = Job("job-16", "launch-inference-rollout", "inference", 1, 6, 70, 0.70, 3.0, 4.0, None,
                 migratable=True, deferrable=False)
    base = normal()
    return replace(base, jobs=base.jobs + (urgent,), scenario="deadline",
                   incidents=("Critical job-16 (6 GPUs) must finish within 4 h",))


def infeasible() -> DataCenterState:
    state = inject_cooling_failure(normal(), loss=0.6)
    pinned = tuple(replace(j, migratable=False, deferrable=False) if j.rack_id == COOLING_FAILURE_RACK else j
                   for j in state.jobs)
    return replace(state, jobs=pinned, scenario="infeasible",
                   incidents=state.incidents + ("rack-04 jobs pinned (data locality); cannot migrate or defer",))


SCENARIOS: Dict[str, Callable[[], DataCenterState]] = {
    "normal": normal,
    "cooling_failure": cooling_failure,
    "power_constraint": power_constraint,
    "deadline": deadline,
    "infeasible": infeasible,
}
