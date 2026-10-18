"""LLM provider abstraction: Crusoe Managed Inference (OpenAI-compatible) or a deterministic mock.

The LLM only plans, delegates, chooses strategies and summarises. It never does
arithmetic, physics, or the final PASS/REJECT.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Protocol

import httpx

from .. import config as C
from . import mock_responses

log = logging.getLogger("datacenteros.llm")


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    data: Dict[str, Any]
    latency_ms: float
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    raw_text: str = ""


@dataclass
class CallStats:
    calls: int = 0
    errors: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_latency_ms: float = 0.0
    log: List[dict] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, agent: str, task: str, response: Optional[LLMResponse], error: Optional[str]) -> None:
        with self._lock:
            self.calls += 1
            entry = {"agent": agent, "task": task, "error": error}
            if response:
                self.tokens_in += response.tokens_in or 0
                self.tokens_out += response.tokens_out or 0
                self.total_latency_ms += response.latency_ms
                entry |= {"latency_ms": round(response.latency_ms, 1),
                          "tokens_in": response.tokens_in, "tokens_out": response.tokens_out}
            else:
                self.errors += 1
            self.log.append(entry)

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "errors": self.errors, "tokens_in": self.tokens_in,
                    "tokens_out": self.tokens_out,
                    "avg_latency_ms": round(self.total_latency_ms / self.calls, 1) if self.calls else 0.0}


class LLMProvider(Protocol):
    name: str
    model: str
    is_mock: bool

    def complete_json(self, *, agent: str, task: str, system_prompt: str, payload: dict) -> LLMResponse: ...


class MockProvider:
    """Deterministic, offline provider. Responses are computed from the payload, never real inference."""
    name = "mock"
    model = "deterministic-mock-v1"
    is_mock = True

    def complete_json(self, *, agent: str, task: str, system_prompt: str, payload: dict) -> LLMResponse:
        start = time.perf_counter()
        handler: Callable[[dict], dict] = mock_responses.HANDLERS[task]
        data = handler(payload)
        return LLMResponse(data, (time.perf_counter() - start) * 1000, raw_text=json.dumps(data))


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from model text (handles code fences / reasoning preambles)."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = _JSON_BLOCK.search(cleaned)
    if not match:
        raise LLMError("model response contained no JSON object")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON from model: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise LLMError("model JSON was not an object")
    return value


class CrusoeProvider:
    """Crusoe Managed Inference via its OpenAI-compatible /chat/completions endpoint."""
    name = "crusoe"
    is_mock = False

    def __init__(self, api_key: str, model: str = C.CRUSOE_DEFAULT_MODEL, base_url: str = C.CRUSOE_BASE_URL,
                 timeout_s: float = C.LLM_TIMEOUT_S, retries: int = C.LLM_RETRIES,
                 client: Optional[httpx.Client] = None) -> None:
        if not api_key:
            raise ValueError("CRUSOE_API_KEY is required for the Crusoe provider")
        self.model = model
        self._retries = retries
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s,
                                              headers={"Authorization": f"Bearer {api_key}"})

    def _post(self, body: dict) -> httpx.Response:
        last: Optional[Exception] = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.post("/chat/completions", json=body)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise LLMError(f"Crusoe API transient HTTP {resp.status_code}")
                return resp
            except (httpx.TimeoutException, httpx.TransportError, LLMError) as exc:
                last = exc
                log.warning("crusoe call failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(min(2.0, 0.5 * 2 ** attempt))
        raise LLMError(f"Crusoe API unavailable after retries: {last}")

    def complete_json(self, *, agent: str, task: str, system_prompt: str, payload: dict) -> LLMResponse:
        body = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt + "\nRespond with a single JSON object only."},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            "response_format": {"type": "json_object"},
        }
        start = time.perf_counter()
        resp = self._post(body)
        if resp.status_code == 400 and "response_format" in resp.text:
            body.pop("response_format")
            resp = self._post(body)
        if resp.status_code >= 400:
            raise LLMError(f"Crusoe API HTTP {resp.status_code}: {resp.text[:200]}")
        latency = (time.perf_counter() - start) * 1000
        try:
            out = resp.json()
            text = out["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"unexpected Crusoe response shape: {exc}") from exc
        usage = out.get("usage") or {}
        return LLMResponse(parse_json_object(text), latency, usage.get("prompt_tokens"),
                           usage.get("completion_tokens"), text)


def provider_from_env() -> LLMProvider:
    """Use Crusoe when CRUSOE_API_KEY is set (and DCOS_LLM_PROVIDER != mock), else the mock."""
    key = os.environ.get("CRUSOE_API_KEY", "").strip()
    if key and os.environ.get("DCOS_LLM_PROVIDER", "crusoe").lower() != "mock":
        return CrusoeProvider(key, model=os.environ.get("CRUSOE_MODEL", C.CRUSOE_DEFAULT_MODEL),
                              base_url=os.environ.get("CRUSOE_BASE_URL", C.CRUSOE_BASE_URL))
    return MockProvider()
