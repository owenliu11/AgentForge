# tests/test_phase1.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import create_run, run_agent
from app.db import SessionLocal
from app.main import app
from app.model_interface import Model
from app.models import AgentRun, RunEvent, RunStep, ToolCall
from app.state_machine import IllegalTransition, transition
from app.tools import ToolContext, ToolError, execute_tool

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "sample_repo"
FAILING_TEST = "tests.test_math.TestMath.test_subtract_fails"

pytestmark = pytest.mark.usefixtures("db")


class LoopingModel(Model):
    """Never finishes; used to hit the max-steps cap."""

    def decide(self, seq, context):
        return {"action": "tool_call", "tool": "files.read", "args": {"path": "README.md"}}


class TraversalModel(Model):
    def decide(self, seq, context):
        if seq == 1:
            return {
                "action": "tool_call",
                "tool": "files.read",
                "args": {"path": "../../etc/passwd"},
            }
        return {"action": "finish", "result": {}}


def load_run(run_id):
    with SessionLocal() as session:
        run = session.get(AgentRun, run_id)
        steps = session.scalars(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)
        ).all()
        calls = session.scalars(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.step_seq)
        ).all()
        events = session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        ).all()
    return run, steps, calls, events


def test_illegal_transitions_raise():
    run_id = create_run(objective="illegal transition test", repo_path="sample_repo")

    with SessionLocal.begin() as session:
        transition(session, run_id, "RUNNING")

    with SessionLocal.begin() as session:
        transition(session, run_id, "COMPLETED")

    with pytest.raises(IllegalTransition):
        with SessionLocal.begin() as session:
            transition(session, run_id, "RUNNING")

    with pytest.raises(IllegalTransition):
        with SessionLocal.begin() as session:
            transition(session, run_id, "FAILED")


def test_queued_run_cannot_jump_to_completed():
    run_id = create_run(objective="queued cannot jump to completed", repo_path="sample_repo")

    with pytest.raises(IllegalTransition):
        with SessionLocal.begin() as session:
            transition(session, run_id, "COMPLETED")


def test_path_traversal_is_rejected(tmp_path):
    ctx = ToolContext(repo_path=FIXTURE_REPO, reports_dir=tmp_path)

    for path in ["../../etc/passwd", "/etc/passwd"]:
        with pytest.raises(ToolError):
            execute_tool("files.read", {"path": path}, ctx)

    with pytest.raises(ToolError):
        execute_tool("report.publish", {"path": "../escape.md"}, ctx)


def test_search_skips_symlinks_out_of_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n")
    (repo / "link.txt").symlink_to(secret)

    ctx = ToolContext(repo_path=repo, reports_dir=tmp_path)
    assert execute_tool("files.search", {"pattern": "SECRET"}, ctx)["matches"] == []


def test_model_cannot_pass_internal_args(tmp_path):
    ctx = ToolContext(repo_path=FIXTURE_REPO, reports_dir=tmp_path)

    with pytest.raises(ToolError, match="Unknown args"):
        execute_tool("tests.run", {"use_docker": False}, ctx)

    with pytest.raises(ToolError, match="Unknown args"):
        execute_tool("files.read", {"path": "README.md", "_repo_path": "/"}, ctx)


def test_traversal_in_a_run_is_reported_to_the_model():
    run_id = create_run(objective="traversal", repo_path="sample_repo")

    assert run_agent(run_id, model=TraversalModel()) == "COMPLETED"

    _, _, calls, _ = load_run(run_id)
    assert "Path rejected" in calls[0].result["error"]


def test_create_run_rejects_repo_outside_root():
    with pytest.raises(ValueError):
        create_run(objective="escape", repo_path="../../..")


def test_max_steps_cap_fails_the_run():
    run_id = create_run(objective="loop forever", repo_path="sample_repo", max_steps=3)

    assert run_agent(run_id, model=LoopingModel()) == "FAILED"

    run, steps, _, _ = load_run(run_id)
    assert len(steps) == 3
    assert "MAX_STEPS_EXCEEDED" in run.error


def test_scripted_run_completes_deterministically():
    outcomes = []

    for _ in range(2):
        run_id = create_run(objective="Analyze fixture repo", repo_path="sample_repo")
        assert run_agent(run_id) == "COMPLETED"

        run, steps, calls, events = load_run(run_id)
        assert [s.status for s in steps] == ["SUCCEEDED"] * 5
        assert all(c.status == "SUCCEEDED" for c in calls)
        assert [e.seq for e in events] == list(range(1, len(events) + 1))

        tests_run = next(c for c in calls if c.tool == "tests.run")
        assert tests_run.result["failed_tests"] == [FAILING_TEST]

        outcomes.append(
            (
                [s.decision for s in steps],
                [(c.tool, c.args, c.result.get("failed_tests")) for c in calls],
                [e.type for e in events],
            )
        )

    assert outcomes[0] == outcomes[1]


def test_posting_run_against_fixture_repo_produces_report():
    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"objective": "Analyze fixture repo via API", "repo_path": "sample_repo"},
        )
        assert response.status_code == 202
        run_id = response.json()["id"]

        # TestClient runs background tasks before returning.
        body = client.get(f"/runs/{run_id}").json()
        assert body["status"] == "COMPLETED", body

        events = client.get(f"/runs/{run_id}/events").json()
        event_types = [event["type"] for event in events]
        assert event_types[:2] == ["run_created", "run_started"]
        assert event_types[-1] == "run_completed"

    [report_path] = body["result"]["reports"]
    assert FAILING_TEST in Path(report_path).read_text(encoding="utf-8")


def test_api_rejects_bad_input():
    with TestClient(app) as client:
        assert client.post("/runs", json={"repo_path": "../.."}).status_code == 422
        assert client.get("/runs/not-a-uuid").status_code == 422
        missing = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/runs/{missing}").status_code == 404
        assert client.get(f"/runs/{missing}/events").status_code == 404
