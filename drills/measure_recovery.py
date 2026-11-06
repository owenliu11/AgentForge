"""
Measure recovery time: from a worker's death to the next worker's claim.

Runs the after_effect crash drill N times with real worker processes and writes
the samples and percentiles to drills/results/recovery.json. Both timestamps
come from the database clock. Uses the same 2 s lease as the drills, and recovery
time tracks the lease length, so report the two together.

    make measure
"""

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import text

from app.agent import create_run
from app.config import settings
from app.db import SessionLocal
from tests.support import (
    ROOT,
    TERMINAL,
    _free_port,
    load_run,
    run_status,
    start_worker,
    wait_for,
)

RESULTS = Path(__file__).parent / "results" / "recovery.json"
LEASE_SECONDS = 2


def start_mock_effects() -> subprocess.Popen:
    port = _free_port()
    settings.mock_effects_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "mock_effects.app:app",
            "--port", str(port), "--log-level", "warning",
        ],
        cwd=ROOT,
    )  # fmt: skip

    def up() -> bool:
        try:
            return httpx.get(f"{settings.mock_effects_url}/stats", timeout=1).is_success
        except httpx.HTTPError:
            return False

    wait_for(up, timeout=15, what="mock_effects to start")
    return proc


def measure_once(index: int, log_dir: Path) -> float:
    """Crash one worker after its side effect; return seconds until the next claim."""
    base = settings.mock_effects_url
    httpx.post(f"{base}/reset", timeout=5).raise_for_status()
    run_id = create_run(objective=f"recovery sample {index}", repo_path="sample_repo")

    crasher = start_worker(log_dir, worker_id=f"a-{index}", env={"AF_CRASH_AT": "after_effect"})
    if crasher.wait(timeout=60) != 137:
        raise RuntimeError(f"worker did not crash:\n{crasher.log()}")

    with SessionLocal() as session:
        killed_at = session.scalar(text("SELECT now()"))

    survivor = start_worker(log_dir, worker_id=f"b-{index}")
    try:
        wait_for(lambda: run_status(run_id) in TERMINAL, timeout=60, what="run to finish")
    finally:
        survivor.stop()

    run, _, _, events = load_run(run_id)
    stats = httpx.get(f"{base}/stats", timeout=5).json()

    if run.status != "COMPLETED" or stats["reports_applied"] != 1:
        raise RuntimeError(f"bad outcome: {run.status}, {stats}")

    second_claim = [e for e in events if e.type == "run_claimed"][1]
    return (second_claim.created_at - killed_at).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()

    mock = start_mock_effects()
    samples = []

    try:
        with tempfile.TemporaryDirectory() as log_dir:
            for index in range(args.runs):
                samples.append(round(measure_once(index, Path(log_dir)), 3))
                print(f"{index + 1}/{args.runs}: {samples[-1]:.3f} s", flush=True)
    finally:
        mock.terminate()
        mock.wait(timeout=10)

    results = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "crash_point": "after_effect",
        "lease_seconds": LEASE_SECONDS,
        "runs": len(samples),
        "extra_reports_applied": 0,  # measure_once raises otherwise
        "p50_seconds": round(statistics.median(samples), 3),
        "p95_seconds": round(statistics.quantiles(samples, n=20, method="inclusive")[18], 3),
        "max_seconds": max(samples),
        "samples_seconds": samples,
    }

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"p50 {results['p50_seconds']} s, p95 {results['p95_seconds']} s -> {RESULTS}")


if __name__ == "__main__":
    main()
