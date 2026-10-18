"""Integration tests: coordinator recursion, memory, LLM providers, tool permissions."""
import json

import httpx
import pytest

from app.agents.coordinator import Coordinator
from app.agents.llm import CallStats, CrusoeProvider, LLMError, MockProvider, parse_json_object, provider_from_env
from app.agents.runtime import InvalidTransition, RunContext
from app.agents.specialists import AgentServices
from app.agents.tools import ToolPermissionError, call_tool
from app.benchmark import run_benchmarks
from app.config import AgentConfig
from app.memory import MemoryStore
from app.scenarios import SCENARIOS, cooling_failure


def services(provider=None, **cfg) -> AgentServices:
    return AgentServices(provider or MockProvider(), MemoryStore(":memory:"),
                         AgentConfig(pacing_s=0, **cfg), CallStats())


def test_cooling_failure_first_plan_rejected_then_recovers():
    svc = services()
    result, final = Coordinator(svc).run(cooling_failure())
    assert result["status"] == "VERIFIED"
    first, second = result["attempts"]
    assert first["verdict"] == "REJECT" and {"rack_power", "memory"} <= set(first["families"])
    assert second["verdict"] == "PASS" and second["signature"] != first["signature"]
    assert result["metrics"]["after"]["hard_violations"] == 0
    assert final.job("job-06").rack_id != "rack-04"
    states = [t["to"] for t in result["transitions"]]
    assert states[:4] == ["INVESTIGATING", "PLANNING", "VALIDATING", "REJECTED"]
    assert "RETRYING" in states and states[-1] == "VERIFIED"


def test_every_plan_and_verdict_event_maps_to_real_attempt():
    result, _ = Coordinator(services()).run(cooling_failure())
    verdicts = [e for e in result["events"] if e["kind"] == "verdict"]
    assert [v["data"]["status"] for v in verdicts] == [
        "REJECT" if a["verdict"] == "REJECT" else "PASS" for a in result["attempts"]]


def test_infeasible_escalates_within_budget():
    result, final = Coordinator(services()).run(SCENARIOS["infeasible"]())
    assert result["status"] == "ESCALATED"
    assert len(result["attempts"]) == 4
    assert final == SCENARIOS["infeasible"]()  # nothing applied


def test_attempt_budget_is_configurable():
    result, _ = Coordinator(services(max_attempts=1)).run(cooling_failure())
    assert result["status"] == "ESCALATED" and len(result["attempts"]) == 1


def test_normal_requires_no_action():
    result, _ = Coordinator(services()).run(SCENARIOS["normal"]())
    assert result["status"] == "VERIFIED" and result["plan"] is None and result["llm"]["calls"] == 0


def test_memory_persists_and_cross_run_memory_skips_known_bad_plan():
    svc = services(use_cross_run_memory=True)
    first, _ = Coordinator(svc).run(cooling_failure())
    assert svc.memory.retrieve_failures(f"cooling_failure|{cooling_failure().incidents[0]}")
    second, _ = Coordinator(svc).run(cooling_failure())
    assert second["status"] == "VERIFIED" and second["rejections"] == 0
    stored = svc.memory.get_run(first["run_id"])
    assert stored["status"] == "VERIFIED" and len(stored["events"]) == len(first["events"])
    assert len(svc.memory.list_attempts()) == 3


def test_llm_budget_falls_back_deterministically():
    result, _ = Coordinator(services(max_llm_calls=0)).run(cooling_failure())
    llm_events = [e for e in result["events"] if e["kind"] == "llm"]
    assert result["status"] == "VERIFIED" and all(e["data"]["source"] == "fallback" for e in llm_events)


class BadProvider(MockProvider):
    name, is_mock = "bad", False

    def complete_json(self, **_):
        raise LLMError("boom")


def test_llm_errors_do_not_break_the_loop():
    result, _ = Coordinator(services(BadProvider())).run(cooling_failure())
    assert result["status"] == "VERIFIED" and result["llm"]["errors"] > 0


def test_tool_permissions_enforced():
    with pytest.raises(ToolPermissionError):
        call_tool("thermal", "verify_plan", cooling_failure(), None)
    with pytest.raises(ToolPermissionError):
        call_tool("coordinator", "plan_search")


def test_state_machine_rejects_illegal_transition():
    ctx = RunContext("r", "f")
    with pytest.raises(InvalidTransition):
        ctx.transition("VERIFIED", "skip")


def test_parse_json_object_handles_reasoning_and_fences():
    assert parse_json_object('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json_object("no json here")


def test_crusoe_provider_parses_response_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "nvidia/test"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"summary": "ok"}'}}],
                                         "usage": {"prompt_tokens": 11, "completion_tokens": 3}})
    client = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler),
                          headers={"Authorization": "Bearer test-key"})
    provider = CrusoeProvider("test-key", model="nvidia/test", client=client)
    resp = provider.complete_json(agent="a", task="final_summary", system_prompt="s", payload={})
    assert resp.data == {"summary": "ok"} and resp.tokens_in == 11


def test_crusoe_provider_raises_on_http_error():
    client = httpx.Client(base_url="https://x", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    provider = CrusoeProvider("k", client=client, retries=0)
    with pytest.raises(LLMError, match="401"):
        provider.complete_json(agent="a", task="t", system_prompt="s", payload={})


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv("CRUSOE_API_KEY", raising=False)
    assert provider_from_env().is_mock
    monkeypatch.setenv("CRUSOE_API_KEY", "k")
    assert provider_from_env().name == "crusoe"
    monkeypatch.setenv("DCOS_LLM_PROVIDER", "mock")
    assert provider_from_env().is_mock


def test_benchmarks_match_expected_outcomes():
    rows = run_benchmarks()
    assert {r["scenario"]: r["status"] for r in rows} == {
        "normal": "VERIFIED", "cooling_failure": "VERIFIED", "power_constraint": "VERIFIED",
        "deadline": "VERIFIED", "infeasible": "ESCALATED"}
    assert all(r["hard_violations_after"] == 0 for r in rows if r["status"] == "VERIFIED")
