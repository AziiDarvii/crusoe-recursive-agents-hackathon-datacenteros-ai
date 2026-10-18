"""Reproducible benchmark suite over all scenarios (same workload and horizon per comparison)."""
import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from .agents.coordinator import Coordinator
from .agents.llm import CallStats, LLMProvider, MockProvider
from .agents.specialists import AgentServices
from .config import AgentConfig
from .memory import MemoryStore
from .scenarios import SCENARIOS

EXPECTED = {"normal": "VERIFIED", "cooling_failure": "VERIFIED", "power_constraint": "VERIFIED",
            "deadline": "VERIFIED", "infeasible": "ESCALATED"}
METRIC_KEYS = ("hard_violations", "thermal_violations", "power_violations", "temp_outlet_max_c",
               "facility_kw_avg", "energy_kwh", "cooling_kw_avg", "pue_avg", "gpu_utilization_pct",
               "jobs_completed", "jobs_missed_deadline")


def run_benchmarks(provider: Optional[LLMProvider] = None, config: Optional[AgentConfig] = None) -> List[dict]:
    config = replace(config or AgentConfig(), pacing_s=0.0, use_cross_run_memory=False)
    rows = []
    for name, build in SCENARIOS.items():
        svc = AgentServices(provider or MockProvider(), MemoryStore(":memory:"), config, CallStats())
        result, _ = Coordinator(svc).run(build())
        row = {"scenario": name, "status": result["status"], "expected": EXPECTED[name],
               "as_expected": result["status"] == EXPECTED[name], "attempts": len(result["attempts"]),
               "rejections": result["rejections"], "llm_calls": result["llm"]["calls"],
               "provider": result["provider"]["name"], "runtime_s": result["runtime_s"],
               "plan": "; ".join(a["description"] for a in (result["plan"] or {}).get("actions", [])),
               "learned_constraints": ",".join(result["learned_constraints"])}
        for key in METRIC_KEYS:
            row[f"{key}_before"] = result["metrics"]["before"][key]
            row[f"{key}_after"] = result["metrics"]["after"][key]
        rows.append(row)
    return rows


def export(rows: List[dict], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, csv_path = out_dir / "results.json", out_dir / "results.csv"
    json_path.write_text(json.dumps(rows, indent=2))
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return [json_path, csv_path]
