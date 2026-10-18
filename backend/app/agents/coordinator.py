"""Coordinator agent: bounded recursive plan -> verify -> learn -> replan loop."""
import uuid
from typing import Optional, Tuple

from ..metrics import compare, compute_metrics
from ..models import DataCenterState, Plan
from ..optimizer import BASE_FAMILIES, STRATEGY_ORDER
from ..safety import Verdict
from ..simulator import apply_plan
from . import schemas
from .llm import CallStats
from .runtime import RunContext
from .specialists import AgentServices, ask_llm, investigate, memory_call, tool

FAST_PATH_OWNER = (("thermal", "thermal", "thermal_fast_path"),
                   ("rack_power", "power", "power_fast_path"),
                   ("critical_deadline", "scheduling", "scheduling_fast_path"))
DEFAULT_OBJECTIVE = "Restore all hard constraints with minimal disruption, energy and deadline impact."


def fingerprint(state: DataCenterState) -> str:
    return f"{state.scenario}|{'|'.join(state.incidents)}"


class Coordinator:
    def __init__(self, svc: AgentServices) -> None:
        self.svc = svc

    # ------------------------------------------------------------------ entry point
    def run(self, state: DataCenterState, objective: str = DEFAULT_OBJECTIVE, run_id: Optional[str] = None,
            on_event=None) -> Tuple[dict, DataCenterState]:
        run_id = run_id or uuid.uuid4().hex[:10]
        self.svc.stats = CallStats()
        ctx = RunContext(run_id, fingerprint(state), self.svc.config.pacing_s,
                         on_event=lambda e: self._persist(run_id, e, on_event))
        self.svc.memory.start_run(run_id, state.scenario, ctx.fingerprint, self.svc.provider.name)
        root = ctx.add_task("coordinator", objective, None, depth=0)
        ctx.emit("info", "coordinator", "objective received", objective, task_id=root)
        before = compute_metrics(state)

        ctx.transition("INVESTIGATING", "read state and delegate investigation")
        incident: Verdict = tool(ctx, "safety", "verify_state", state, task_id=root,
                                 summary=lambda v: f"{v.status}: {len(v.violations)} hard violation(s)")
        if incident.passed:
            ctx.transition("VERIFIED", "no hard-constraint violations; no action required")
            return self._finish(ctx, state, state, before, None, "No hard-constraint violations; no action taken.")

        self._load_memory(ctx, root)
        families = incident.families()
        plan_req = ask_llm(self.svc, ctx, "coordinator", "decompose",
                           {"objective": objective, "incidents": list(state.incidents),
                            "incident_families": list(families),
                            "violations": [v.to_dict() for v in incident.violations]},
                           schemas.validate_decompose, root)
        investigate(self.svc, ctx, state, plan_req["tasks"], root)
        ctx.transition("PLANNING", "specialist findings combined; generating candidate plan")
        final_state, plan = self._attempt(ctx, state, frozenset(families), depth=1, parent=root, last=None)
        return self._finish(ctx, state, final_state, before, plan, None)

    # ------------------------------------------------------------------ recursion
    def _attempt(self, ctx: RunContext, state: DataCenterState, incident: frozenset, depth: int,
                 parent: str, last: Optional[dict]) -> Tuple[DataCenterState, Optional[Plan]]:
        cfg = self.svc.config
        if len(ctx.attempts) >= cfg.max_attempts or depth > cfg.max_depth:
            return self._escalate(ctx, state, f"retry budget exhausted ({len(ctx.attempts)} attempts, depth {depth - 1})")
        number = len(ctx.attempts) + 1
        tid = ctx.add_task("coordinator", f"Attempt {number}", parent, depth)
        if ctx.state != "PLANNING":
            ctx.transition("PLANNING", f"attempt {number} at recursion depth {depth}")

        plan, strategy, author, search_note = self._propose(ctx, state, incident, number, last, tid)
        if plan is None and strategy is None:
            return self._escalate(ctx, state, search_note)
        if plan is None:
            ctx.exhausted.add(strategy)
            record = self._record(ctx, number, depth, strategy, author, None, "NO_FEASIBLE_PLAN", [], tid)
            ctx.finish_task(tid, "failed", search_note)
            ctx.transition("REJECTED", f"no feasible plan with strategy '{strategy}': {search_note}")
            return self._retry(ctx, state, incident, depth, tid, record)

        ctx.emit("plan", author, f"plan proposed ({strategy})", plan.rationale, plan.to_dict(), tid)
        ctx.transition("VALIDATING", "independent safety verification of the candidate plan")
        verdict: Verdict = tool(ctx, "safety", "verify_plan", state, plan, task_id=tid,
                                summary=lambda v: f"{v.status} " + "; ".join(x.code for x in v.violations))
        ctx.emit("verdict", "safety", f"Safety verdict: {verdict.status}",
                 "; ".join(v.message for v in verdict.violations) or "all hard constraints satisfied",
                 verdict.to_dict(), tid)
        if not verdict.passed:
            record = self._record(ctx, number, depth, strategy, author, plan, "REJECT", verdict.violations, tid)
            new = set(verdict.families()) - ctx.learned - BASE_FAMILIES
            ctx.learned |= set(verdict.families())
            ctx.tabu.add(plan.signature())
            ctx.finish_task(tid, "rejected", "; ".join(v.code for v in verdict.violations))
            ctx.transition("REJECTED", f"plan violates {', '.join(verdict.families())}; "
                                       f"learned new constraint families {sorted(new) or 'none'}")
            return self._retry(ctx, state, incident, depth, tid, record)

        ctx.transition("SIMULATING", "apply plan to a copy of the twin and measure effects")
        candidate = apply_plan(state, plan)
        impact = tool(ctx, "power", "estimate_energy_impact", state, candidate, task_id=tid,
                      summary=lambda i: f"energy {i['energy_kwh']['before']} → {i['energy_kwh']['after']} kWh")
        recheck: Verdict = tool(ctx, "safety", "verify_state", candidate, task_id=tid,
                                summary=lambda v: f"post-apply {v.status}")
        if not recheck.passed:  # defensive: should be impossible after a PASS
            record = self._record(ctx, number, depth, strategy, author, plan, "REJECT", recheck.violations, tid)
            ctx.tabu.add(plan.signature())
            ctx.transition("REJECTED", "post-apply verification failed")
            return self._retry(ctx, state, incident, depth, tid, record)
        self._record(ctx, number, depth, strategy, author, plan, "PASS", [], tid, impact)
        ctx.finish_task(tid, "verified", "; ".join(a.describe() for a in plan.actions))
        ctx.transition("VERIFIED", f"plan committed after {number} attempt(s); all hard constraints pass")
        ctx.emit("commit", "coordinator", "plan committed to digital twin",
                 "; ".join(a.describe() for a in plan.actions), plan.to_dict(), tid)
        return candidate, plan

    def _retry(self, ctx: RunContext, state: DataCenterState, incident: frozenset, depth: int,
               parent: str, record: dict) -> Tuple[DataCenterState, Optional[Plan]]:
        if len(ctx.attempts) >= self.svc.config.max_attempts:
            return self._escalate(ctx, state, f"no verified plan within {self.svc.config.max_attempts} attempts")
        ctx.transition("RETRYING", f"recursing with failure context (depth {depth + 1})")
        return self._attempt(ctx, state, incident, depth + 1, parent, record)

    def _propose(self, ctx: RunContext, state: DataCenterState, incident: frozenset, number: int,
                 last: Optional[dict], tid: str):
        """Return (plan, strategy, author, note). (None, None, _, note) means escalate."""
        if last is None:
            for family, agent, fast_tool in FAST_PATH_OWNER:
                if family in incident:
                    plan = tool(ctx, agent, fast_tool, state, task_id=tid,
                                summary=lambda p: p.rationale if p else "no fast-path move available")
                    if plan is not None and plan.signature() not in ctx.tabu:
                        return plan, plan.strategy, agent, ""
                    break
            strategy = STRATEGY_ORDER[0]
        else:
            failures = [a for a in ctx.attempts if a["verdict"] != "PASS"]
            memory_call(self.svc, ctx, "retrieve_failures", lambda: failures,
                        f"{len(failures)} prior failure(s), {len(ctx.tabu)} tabu plan(s), "
                        f"learned {sorted(ctx.learned - BASE_FAMILIES)}", tid)
            available = [s for s in STRATEGY_ORDER if s not in ctx.exhausted]
            decision = ask_llm(self.svc, ctx, "coordinator", "replan", {
                "attempt": number, "incident_families": sorted(incident),
                "last_outcome": {k: last[k] for k in ("verdict", "families", "strategy")},
                "last_violations": [v["message"] for v in last["violations"]][:6],
                "history": [{k: a[k] for k in ("attempt", "strategy", "verdict", "families")} for a in failures],
                "learned_constraints": sorted(ctx.learned | incident),
                "strategies_available": available, "strategies_exhausted": sorted(ctx.exhausted),
            }, lambda d: schemas.validate_replan(d, available), tid)
            if decision["decision"] == "escalate":
                return None, None, "coordinator", f"coordinator escalated: {decision['rationale']}"
            strategy = decision["strategy"]
        active = frozenset(BASE_FAMILIES | incident | ctx.learned)
        result = tool(ctx, "scheduling", "plan_search", state, strategy, active, frozenset(ctx.tabu),
                      self.svc.config.weights, task_id=tid,
                      summary=lambda r: f"evaluated {r.evaluated} candidates; "
                                        + ("found plan" if r.plan else r.reason))
        return result.plan, strategy, "scheduling", result.reason

    # ------------------------------------------------------------------ helpers
    def _record(self, ctx: RunContext, number: int, depth: int, strategy: str, author: str,
                plan: Optional[Plan], verdict: str, violations, tid: str, impact: Optional[dict] = None) -> dict:
        vdicts = [v.to_dict() for v in violations]
        record = {"attempt": number, "depth": depth, "strategy": strategy, "author": author,
                  "signature": plan.signature() if plan else None, "verdict": verdict,
                  "families": sorted({v["family"] for v in vdicts}), "violations": vdicts,
                  "plan": plan.to_dict() if plan else None, "energy_impact": impact}
        ctx.attempts.append(record)
        memory_call(self.svc, ctx, "record_attempt",
                    lambda: self.svc.memory.record_attempt(ctx.run_id, ctx.fingerprint, record),
                    f"attempt {number}: {verdict}" + (f" ({', '.join(record['families'])})" if vdicts else ""), tid,
                    data={"attempt": record})
        return record

    def _load_memory(self, ctx: RunContext, root: str) -> None:
        prior = memory_call(self.svc, ctx, "retrieve_failures",
                            lambda: self.svc.memory.retrieve_failures(ctx.fingerprint, ctx.run_id),
                            "query prior runs for this scenario", root)
        if self.svc.config.use_cross_run_memory and prior:
            ctx.tabu |= {p["signature"] for p in prior if p["signature"]}
            ctx.learned |= {f for p in prior for f in p["families"]}
            ctx.emit("memory", "memory", "cross-run memory applied",
                     f"{len(ctx.tabu)} known-invalid plan(s) tabu; constraints {sorted(ctx.learned)}", task_id=root)
        else:
            ctx.emit("memory", "memory", f"{len(prior)} prior failure(s) found",
                     "cross-run memory disabled; this run learns from its own failures" if prior else "", task_id=root)

    def _escalate(self, ctx: RunContext, state: DataCenterState, reason: str) -> Tuple[DataCenterState, None]:
        ctx.transition("ESCALATED", f"human intervention required: {reason}")
        ctx.emit("escalate", "coordinator", "Escalated to human operator", reason)
        ctx.escalation_reason = reason
        return state, None

    def _persist(self, run_id: str, event: dict, forward) -> None:
        self.svc.memory.add_event(run_id, event)
        if forward:
            forward(event)

    def _finish(self, ctx: RunContext, incident_state: DataCenterState, final_state: DataCenterState,
                before: dict, plan: Optional[Plan], fixed_summary: Optional[str]) -> Tuple[dict, DataCenterState]:
        after = compute_metrics(final_state)
        delta = compare(before, after)
        rejections = sum(1 for a in ctx.attempts if a["verdict"] != "PASS")
        if fixed_summary:
            summary = fixed_summary
        else:
            summary = ask_llm(self.svc, ctx, "coordinator", "final_summary", {
                "outcome": ctx.state, "attempts": len(ctx.attempts), "rejections": rejections,
                "reason": ctx.escalation_reason,
                "actions": [a.describe() for a in plan.actions] if plan else [],
                "delta": {k: delta[k] for k in ("hard_violations", "temp_outlet_max_c", "energy_kwh",
                                                "jobs_missed_deadline", "cooling_kw_avg") if k in delta},
            }, schemas.validate_summary)["summary"]
        ctx.finish_task(ctx.tasks[0]["id"], ctx.state.lower(), summary)
        result = {
            "run_id": ctx.run_id, "scenario": incident_state.scenario, "status": ctx.state, "summary": summary,
            "escalation_reason": ctx.escalation_reason, "incidents": list(incident_state.incidents),
            "provider": {"name": self.svc.provider.name, "model": self.svc.provider.model,
                         "mock": self.svc.provider.is_mock},
            "llm": self.svc.stats.snapshot(), "transitions": ctx.transitions, "tasks": ctx.tasks,
            "attempts": ctx.attempts, "plan": plan.to_dict() if plan else None,
            "learned_constraints": sorted(ctx.learned - BASE_FAMILIES),
            "metrics": {"before": before, "after": after}, "comparison": delta,
            "rejections": rejections, "runtime_s": round(ctx.elapsed_ms() / 1000, 3),
        }
        ctx.emit("info", "coordinator", "run complete", summary)
        self.svc.memory.finish_run(ctx.run_id, ctx.state, summary, result)
        result["events"] = ctx.events
        return result, final_state
