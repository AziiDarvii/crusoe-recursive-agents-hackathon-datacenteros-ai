"""Deterministic mock 'LLM' outputs derived from the structured payload.

These are template responses, clearly labelled as mock in the UI. They let the
full system run offline and reproducibly; they are not model inference.
"""
from typing import Callable, Dict

STRATEGY_HINTS = {
    "constrained_search": "re-place workloads under every learned constraint",
    "defer_flexible": "also defer flexible, deadline-tolerant jobs",
    "power_cap": "also apply simulated GPU power caps on affected racks",
    "combined": "combine migration, deferral and power caps",
}


def decompose(p: dict) -> dict:
    racks = ", ".join(sorted({v["subject"] for v in p.get("violations", [])})) or "the facility"
    return {
        "tasks": [
            {"agent": "thermal", "objective": f"Quantify thermal headroom and heat sources around {racks}."},
            {"agent": "power", "objective": "Check rack and facility power headroom and cooling efficiency."},
            {"agent": "scheduling", "objective": "Find free GPU capacity and flexible jobs that can move or wait."},
        ],
        "rationale": f"Incident families {p.get('incident_families')} span cooling, power and placement, "
                     "so each specialist investigates its domain before any plan is proposed.",
    }


def replan(p: dict) -> dict:
    available = p.get("strategies_available", [])
    if not available:
        return {"decision": "escalate", "strategy": None,
                "rationale": "Every strategy has been exhausted without a verified plan; a human must decide."}
    last = p.get("last_outcome", {})
    incident = set(p.get("incident_families", []))
    choice = available[0]
    if last.get("verdict") == "NO_FEASIBLE_PLAN" and incident & {"rack_power", "facility_power"} \
            and "power_cap" in available:
        choice = "power_cap"
    reasons = ", ".join(last.get("families") or [last.get("verdict", "unknown")])
    return {"decision": "retry", "strategy": choice,
            "rationale": f"Previous attempt failed on {reasons}. Retry with '{choice}' to "
                         f"{STRATEGY_HINTS[choice]}, keeping learned constraints {p.get('learned_constraints')}."}


def _report(agent: str) -> Callable[[dict], dict]:
    def handler(p: dict) -> dict:
        f = p["findings"]
        if agent == "thermal":
            risk = f["at_risk"]
            summary = (f"{len(risk)} rack(s) exceed thermal limits: " + ", ".join(
                f"{r['rack']} peak outlet {r['peak_outlet_c']}°C / inlet {r['peak_inlet_c']}°C" for r in risk)
                       if risk else "All racks are within inlet and outlet limits.")
            rec = (f"Relieve {risk[0]['rack']} by moving or reducing its largest heat source "
                   f"({risk[0]['top_jobs'][0] if risk[0]['top_jobs'] else 'n/a'}).") if risk else "No thermal action."
            risks = [f"{r['rack']} cooling at {r['cooling_load_pct']}% of derated capacity" for r in risk]
        elif agent == "power":
            tight = [r for r in f["racks"] if r["headroom_kw"] < 1.0]
            summary = (f"Facility peak {f['facility_peak_kw']} kW of {f['facility_limit_kw']} kW, PUE "
                       f"{f['pue']}. {len(tight)} rack(s) have under 1 kW of power headroom.")
            risks = [f"{r['rack']} headroom {r['headroom_kw']} kW (budget {r['limit_kw']} kW)" for r in tight]
            rec = "Prefer target racks with power headroom; caps trade throughput for kW."
        else:
            free = [r for r in f["racks"] if r["free_gpus"] > 0]
            summary = (f"{sum(r['free_gpus'] for r in free)} GPUs free across {len(free)} racks; "
                       f"{len(f['flexible_jobs'])} flexible jobs; {len(f['queued_critical'])} critical jobs queued.")
            risks = [f"{j['job']} slack {j['slack_h']} h" for j in f["flexible_jobs"] if j["slack_h"] < 1.0]
            rec = "Move migratable jobs only to racks with enough free GPUs and GPU memory."
        return {"summary": summary, "risks": risks, "recommendation": rec}
    return handler


def final_summary(p: dict) -> dict:
    if p["outcome"] != "VERIFIED":
        return {"summary": f"Escalated to a human after {p['attempts']} attempt(s): {p.get('reason')}. "
                           "No change was applied to the simulated data center."}
    d = p["delta"]

    def fmt(key: str, unit: str) -> str:
        x = d.get(key)
        return f"{x['before']}→{x['after']}{unit}" if x else "n/a"

    return {"summary": (
        f"Applied plan [{'; '.join(p['actions']) or 'no action'}] after {p['attempts']} attempt(s) and "
        f"{p['rejections']} rejection(s). Hard violations {fmt('hard_violations', '')}, max outlet "
        f"{fmt('temp_outlet_max_c', '°C')}, energy {fmt('energy_kwh', ' kWh')}, deadline misses "
        f"{fmt('jobs_missed_deadline', '')}. All hard constraints verified in simulation.")}


HANDLERS: Dict[str, Callable[[dict], dict]] = {
    "decompose": decompose,
    "replan": replan,
    "thermal_report": _report("thermal"),
    "power_report": _report("power"),
    "scheduling_report": _report("scheduling"),
    "final_summary": final_summary,
}
