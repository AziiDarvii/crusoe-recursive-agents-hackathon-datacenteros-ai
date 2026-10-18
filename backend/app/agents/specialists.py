"""LLM gateway (budgeted, validated, with deterministic fallback) and specialist investigations."""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from ..config import AgentConfig
from ..memory import MemoryStore
from ..models import DataCenterState
from . import mock_responses, schemas
from .llm import CallStats, LLMError, LLMProvider
from .prompts import SYSTEM_PROMPTS, TOOL_PERMISSIONS
from .runtime import RunContext
from .tools import ToolPermissionError, call_tool

log = logging.getLogger("datacenteros.agents")

SPECIALIST_TOOL = {"thermal": "analyze_thermal", "power": "analyze_power", "scheduling": "analyze_capacity"}


@dataclass
class AgentServices:
    provider: LLMProvider
    memory: MemoryStore
    config: AgentConfig
    stats: CallStats


def ask_llm(svc: AgentServices, ctx: RunContext, agent: str, task: str, payload: dict,
            validate: Callable[[Dict[str, Any]], Dict[str, Any]], task_id: str | None = None) -> Dict[str, Any]:
    """Call the LLM for a structured decision; fall back to the deterministic policy on any failure."""
    source = svc.provider.name
    if ctx.llm_calls >= svc.config.max_llm_calls:
        data, source, error, response = mock_responses.HANDLERS[task](payload), "fallback", "LLM budget exhausted", None
    else:
        ctx.llm_calls += 1
        error, response = None, None
        try:
            response = svc.provider.complete_json(agent=agent, task=task, system_prompt=SYSTEM_PROMPTS[task],
                                                  payload=payload)
            data = validate(response.data)
        except (LLMError, ValueError) as exc:
            error = str(exc)
            log.warning("LLM %s/%s failed validation or call: %s", agent, task, exc)
            data, source = validate(mock_responses.HANDLERS[task](payload)), "fallback"
        svc.stats.record(agent, task, response, error)
    meta = {"task": task, "provider": svc.provider.name, "model": svc.provider.model, "source": source,
            "mock": svc.provider.is_mock or source == "fallback", "error": error,
            "latency_ms": round(response.latency_ms, 1) if response else None,
            "tokens_in": response.tokens_in if response else None,
            "tokens_out": response.tokens_out if response else None, "output": data}
    label = "mock LLM" if meta["mock"] else svc.provider.model
    ctx.emit("llm", agent, f"{agent}: {task} ({label})", error or "", meta, task_id)
    return data


def tool(ctx: RunContext, agent: str, name: str, *args: Any, task_id: str | None = None,
         summary: Callable[[Any], str] = lambda _: "", **kwargs: Any) -> Any:
    """Permission-checked tool call that is always visible in the event log."""
    try:
        result = call_tool(agent, name, *args, **kwargs)
    except ToolPermissionError as exc:
        ctx.emit("error", agent, f"permission denied: {name}", str(exc), task_id=task_id)
        raise
    ctx.emit("tool", agent, f"{agent} → {name}", summary(result), {"tool": name}, task_id)
    return result


def memory_call(svc: AgentServices, ctx: RunContext, name: str, fn: Callable[[], Any],
                summary: str, task_id: str | None = None, data: dict | None = None) -> Any:
    if name not in TOOL_PERMISSIONS["memory"]:
        raise ToolPermissionError(f"memory may not call {name}")
    result = fn()
    ctx.emit("memory", "memory", f"memory → {name}", summary, {"tool": name, **(data or {})}, task_id)
    return result


def investigate(svc: AgentServices, ctx: RunContext, state: DataCenterState, tasks: List[dict],
                parent: str) -> Dict[str, dict]:
    """Run each delegated specialist task sequentially (bounded, no uncontrolled parallelism)."""
    reports = {}
    for task in tasks:
        agent = task["agent"]
        tid = ctx.add_task(agent, task["objective"], parent, depth=1)
        ctx.emit("delegate", "coordinator", f"delegate → {agent}", task["objective"], task_id=tid)
        findings = tool(ctx, agent, SPECIALIST_TOOL[agent], state, task_id=tid,
                        summary=lambda f: _findings_summary(agent, f))
        report = ask_llm(svc, ctx, agent, f"{agent}_report", {"agent": agent, "findings": findings},
                         schemas.validate_report, tid)
        reports[agent] = {"findings": findings, "report": report}
        ctx.finish_task(tid, "done", report["summary"])
        ctx.pace()
    return reports


def _findings_summary(agent: str, f: dict) -> str:
    if agent == "thermal":
        return f"{len(f['at_risk'])} rack(s) beyond thermal limits"
    if agent == "power":
        return f"facility peak {f['facility_peak_kw']} kW, PUE {f['pue']}"
    return f"{sum(r['free_gpus'] for r in f['racks'])} free GPUs, {len(f['flexible_jobs'])} flexible jobs"
