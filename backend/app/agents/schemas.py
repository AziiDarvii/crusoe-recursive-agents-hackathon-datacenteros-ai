"""Validation of LLM JSON outputs. Invalid output raises ValueError -> deterministic fallback."""
from typing import Any, Dict, Iterable

from .prompts import SPECIALISTS


def _text(data: Dict[str, Any], key: str, max_len: int = 600) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing string field '{key}'")
    return value.strip()[:max_len]


def validate_decompose(data: Dict[str, Any]) -> Dict[str, Any]:
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")
    clean, seen = [], set()
    for task in tasks:
        if not isinstance(task, dict) or task.get("agent") not in SPECIALISTS:
            continue
        if task["agent"] in seen:
            continue
        seen.add(task["agent"])
        clean.append({"agent": task["agent"], "objective": _text(task, "objective", 240)})
    if not clean:
        raise ValueError("no valid specialist tasks")
    for agent in SPECIALISTS:  # every domain must be investigated
        if agent not in seen:
            clean.append({"agent": agent, "objective": f"Investigate {agent} constraints for the incident."})
    return {"tasks": clean, "rationale": _text(data, "rationale")}


def validate_report(data: Dict[str, Any]) -> Dict[str, Any]:
    risks = data.get("risks", [])
    if not isinstance(risks, list):
        raise ValueError("risks must be a list")
    return {"summary": _text(data, "summary"), "risks": [str(r)[:200] for r in risks[:6]],
            "recommendation": _text(data, "recommendation")}


def validate_replan(data: Dict[str, Any], available: Iterable[str]) -> Dict[str, Any]:
    decision = data.get("decision")
    if decision not in ("retry", "escalate"):
        raise ValueError("decision must be retry or escalate")
    rationale = _text(data, "rationale")
    if decision == "escalate":
        return {"decision": "escalate", "strategy": None, "rationale": rationale}
    strategy = data.get("strategy")
    if strategy not in set(available):
        raise ValueError(f"strategy {strategy!r} is not available")
    return {"decision": "retry", "strategy": strategy, "rationale": rationale}


def validate_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"summary": _text(data, "summary", 1200)}
