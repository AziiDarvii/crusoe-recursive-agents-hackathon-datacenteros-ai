"""Rerun the benchmark suite and export CSV + JSON.

Usage:  .venv/bin/python scripts/benchmark.py [--out benchmarks]
Uses Crusoe if CRUSOE_API_KEY is set, otherwise the deterministic mock provider.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.llm import provider_from_env  # noqa: E402
from app.benchmark import export, run_benchmarks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "benchmarks"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    provider = provider_from_env()
    rows = run_benchmarks(provider)
    for row in rows:
        logging.info("%-17s %-9s attempts=%d rejections=%d violations %s->%s energy %s->%s kWh  %s",
                     row["scenario"], row["status"], row["attempts"], row["rejections"],
                     row["hard_violations_before"], row["hard_violations_after"],
                     row["energy_kwh_before"], row["energy_kwh_after"], row["plan"] or "-")
    for path in export(rows, Path(args.out)):
        logging.info("wrote %s", path)
    return 0 if all(r["as_expected"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
