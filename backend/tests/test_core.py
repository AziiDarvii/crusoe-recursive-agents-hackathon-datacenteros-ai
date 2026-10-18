"""Unit tests: physics, plan application, safety verification, optimizer."""
from dataclasses import replace

import pytest

from app import config as C
from app.config import ObjectiveWeights
from app.metrics import compare, compute_metrics
from app.models import Action, Plan
from app.optimizer import BASE_FAMILIES, search, thermal_fast_path
from app.safety import verify, verify_state
from app.scenarios import SCENARIOS, cooling_failure, inject_cooling_failure, normal
from app.simulator import PlanApplicationError, apply_plan, rack_thermal, simulate


def plan(*actions: Action) -> Plan:
    return Plan(tuple(actions), "test", "test", "test")


def test_rack_thermal_follows_documented_model():
    rack = normal().rack("rack-01")
    inlet, outlet, cooling = rack_thermal(rack, 8.0)
    assert inlet == pytest.approx(rack.supply_air_c)                       # within capacity: no recirculation
    assert outlet == pytest.approx(inlet + 8.0 / C.AIRFLOW_KW_PER_C)
    hot = replace(rack, cooling_derate=0.5)                                  # 5 kW capacity
    inlet2, _, _ = rack_thermal(hot, 8.0)
    assert inlet2 == pytest.approx(rack.supply_air_c + C.RECIRC_C_PER_KW * 3.0)


def test_normal_scenario_passes_all_hard_constraints():
    assert verify_state(normal()).passed


def test_cooling_failure_reduces_capacity_by_30_percent_and_creates_thermal_violation():
    state = cooling_failure()
    assert state.rack("rack-04").cooling_derate == pytest.approx(0.7)
    verdict = verify_state(state)
    assert not verdict.passed
    assert verdict.families() == ("thermal",)


def test_inject_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        inject_cooling_failure(normal(), "rack-99")
    with pytest.raises(ValueError):
        inject_cooling_failure(normal(), loss=1.5)


def test_apply_plan_is_pure():
    state = cooling_failure()
    new = apply_plan(state, plan(Action("migrate", "job-06", rack_id="rack-06")))
    assert state.job("job-06").rack_id == "rack-04"
    assert new.job("job-06").rack_id == "rack-06"
    assert new.job("job-06").delay_h == pytest.approx(C.MIGRATION_RESTORE_H)


@pytest.mark.parametrize("action,reason", [
    (Action("migrate", "job-99", rack_id="rack-01"), "unknown job"),
    (Action("migrate", "job-06", rack_id="rack-04"), "already on target"),
    (Action("defer", "job-06", start_h=1.0), "not deferrable"),
    (Action("power_cap", "rack-01", cap=0.2), "cap must be"),
    (Action("schedule", "job-01", rack_id="rack-02"), "already placed"),
])
def test_invalid_actions_raise(action, reason):
    with pytest.raises(PlanApplicationError, match=reason):
        apply_plan(cooling_failure(), plan(action))


def test_safety_rejects_power_and_memory_violations_with_machine_readable_reasons():
    verdict = verify(cooling_failure(), plan(Action("migrate", "job-06", rack_id="rack-07")))
    assert verdict.status == "REJECT"
    codes = {v.code for v in verdict.violations}
    assert {"RACK_POWER_LIMIT", "MEMORY_INCOMPATIBLE"} <= codes
    power = next(v for v in verdict.violations if v.code == "RACK_POWER_LIMIT")
    assert power.value > power.limit
    assert verdict.to_dict()["checks"]


def test_safety_rejects_invalid_action_and_gpu_overallocation():
    assert verify(normal(), plan(Action("migrate", "nope", rack_id="rack-01"))).violations[0].code == "INVALID_ACTION"
    over = verify(normal(), plan(Action("migrate", "job-12", rack_id="rack-02")))
    assert "GPU_OVERALLOCATION" in {v.code for v in over.violations}


def test_safety_flags_critical_deadline_miss():
    verdict = verify_state(SCENARIOS["deadline"]())
    assert [v.code for v in verdict.violations] == ["CRITICAL_DEADLINE_MISS"]


def test_power_cap_slows_jobs_sublinearly():
    state = SCENARIOS["power_constraint"]()
    capped = apply_plan(state, plan(Action("power_cap", "rack-02", cap=0.8)))
    before, after = simulate(state).jobs["job-03"], simulate(capped).jobs["job-03"]
    assert after.completion_h > before.completion_h
    assert after.completion_h < before.completion_h / 0.8                    # sub-linear slowdown


def test_thermal_fast_path_is_locally_reasonable_but_unsafe():
    state = cooling_failure()
    proposal = thermal_fast_path(state, simulate(state))
    assert proposal.actions[0] == Action("migrate", "job-06", rack_id="rack-07")
    assert not verify(state, proposal).passed


def test_search_respects_learned_constraints_and_tabu():
    state = cooling_failure()
    greedy = thermal_fast_path(state, simulate(state))
    active = frozenset(BASE_FAMILIES | {"rack_power", "memory"})
    result = search(state, "constrained_search", active, frozenset({greedy.signature()}), ObjectiveWeights())
    assert result.plan is not None and result.plan.signature() != greedy.signature()
    assert verify(state, result.plan).passed


def test_search_reports_infeasible():
    result = search(SCENARIOS["infeasible"](), "combined", frozenset(BASE_FAMILIES), frozenset(), ObjectiveWeights())
    assert result.plan is None and "no plan" in result.reason


def test_metrics_and_compare_are_computed_not_fabricated():
    before = compute_metrics(cooling_failure())
    after = compute_metrics(apply_plan(cooling_failure(), plan(Action("migrate", "job-06", rack_id="rack-06"))))
    delta = compare(before, after)
    assert before["thermal_violations"] == 1 and after["thermal_violations"] == 0
    expected_pct = round(100 * (after["energy_kwh"] - before["energy_kwh"]) / before["energy_kwh"], 2)
    assert delta["energy_kwh"]["pct"] == expected_pct
    assert delta["thermal_violations"]["pct"] == -100.0
