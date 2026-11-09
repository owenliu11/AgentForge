"""Black-box readiness example for a future TestForge adapter; no app imports."""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_terminal(client: httpx.Client, api: str, run_id: str, evidence: dict) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"{api}/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        evidence.setdefault("runs", {})[run_id] = run
        if run["status"] in {"COMPLETED", "FAILED"}:
            return run
        time.sleep(0.2)
    raise TimeoutError(f"Run {run_id} did not finish within 90s; last state: {run}")


def run_checks(evidence: dict) -> None:
    api = os.environ.get("API_BASE_URL", "http://127.0.0.1:18010").rstrip("/")
    effects = os.environ.get("EFFECTS_BASE_URL", "http://127.0.0.1:18011").rstrip("/")
    evidence.update(api_base_url=api, effects_base_url=effects, checks=[])
    # This helper submits synthetic runs; don't accidentally target a public deployment.
    for url in (api, effects):
        check(urlsplit(url).hostname in {"localhost", "127.0.0.1"}, "Use an isolated local stack")
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(f"{api}/health")
        check(response.status_code == 200, "API/DB health failed")
        check(response.json() == {"status": "ok", "db": "up"}, "Unexpected health contract")
        check(client.get(f"{api}/demo").status_code == 404, "Demo controls must be disabled")
        before = client.get(f"{effects}/stats")
        before.raise_for_status()
        evidence["effects_before"] = before.json()
        response = client.post(
            f"{api}/runs", json={"objective": "TestForge smoke", "repo_path": "sample_repo"}
        )
        check(response.status_code == 202, f"Run submission failed: {response.text}")
        run_id = response.json()["id"]
        run = wait_terminal(client, api, run_id, evidence)
        check(run["status"] == "COMPLETED", f"Expected completion: {run}")
        events_response = client.get(f"{api}/runs/{run_id}/events")
        events_response.raise_for_status()
        events = events_response.json()
        evidence["events"] = events
        check([e["seq"] for e in events] == list(range(1, len(events) + 1)), "Event order/gap")
        check(events[0]["type"] == "run_created", "Missing creation event")
        check(events[-1]["type"] == "run_completed", "Missing completion event")
        [report_url] = run["result"]["reports"]
        check(report_url.startswith(f"{effects}/reports/"), "Report URL is not host-accessible")
        report_response = client.get(report_url)
        report_response.raise_for_status()
        evidence["report"] = report_response.json()
        check("test_subtract_fails" in evidence["report"]["body"], "Known failure not reported")
        stats_response = client.get(f"{effects}/stats")
        stats_response.raise_for_status()
        evidence["effects_after"] = stats_response.json()
        check(
            evidence["effects_after"]["reports_applied"]
            == evidence["effects_before"]["reports_applied"] + 1,
            "Expected exactly one newly applied report in this serial smoke scenario",
        )
        evidence["checks"].append("completed run, ordered events and known-failure report")
        response = client.post(f"{api}/runs", json={"repo_path": "../../outside"})
        check(response.status_code == 422, "Out-of-root repo must be rejected")
        evidence["checks"].append("invalid repo rejected")
        missing = "00000000-0000-0000-0000-000000000000"
        check(client.get(f"{api}/runs/{missing}").status_code == 404, "Missing run contract")
        evidence["checks"].append("missing run returns 404")
        response = client.post(f"{api}/runs", json={"repo_path": "sample_repo", "max_steps": 1})
        check(response.status_code == 202, "Step-limit scenario was not accepted")
        run = wait_terminal(client, api, response.json()["id"], evidence)
        check(run["status"] == "FAILED", "Step limit must fail the run")
        check("MAX_STEPS_EXCEEDED" in (run["error"] or ""), "Unexpected failure reason")
        evidence["checks"].append("step limit fails with explicit reason")


def main() -> int:
    evidence = {"started_at": datetime.now(UTC).isoformat(), "passed": False}
    output = Path(os.environ.get("SMOKE_REPORT", "reports/testforge/smoke.json"))
    try:
        run_checks(evidence)
        evidence["passed"] = True
        print("TestForge readiness smoke: 4 scenarios passed")
        return 0
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        print(evidence["error"], file=sys.stderr)
        return 1
    finally:
        evidence["finished_at"] = datetime.now(UTC).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(f"Evidence: {output}")


if __name__ == "__main__":
    sys.exit(main())
