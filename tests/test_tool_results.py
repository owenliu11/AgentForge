"""Regression checks: infrastructure failures must never become green reports."""

import subprocess
from unittest.mock import Mock

import httpx
import pytest

from app.config import settings
from app.tools import ToolContext, ToolError, _render_report, report_publish
from app.tools import tests_run as run_tests
from tests.support import require_test_database


def test_docker_launch_failure_is_a_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.tools.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 125, "", "daemon unavailable"),
    )
    with pytest.raises(ToolError, match="exit 125"):
        run_tests(ToolContext(repo_path=tmp_path, reports_dir=tmp_path))


def test_unittest_failure_remains_a_valid_test_result(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.tools.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 1, "", "FAIL: test_example (tests.test_sample.Sample.test_example)"
        ),
    )
    result = run_tests(ToolContext(repo_path=tmp_path, reports_dir=tmp_path))
    assert result["returncode"] == 1
    assert result["failed_tests"] == ["tests.test_sample.Sample.test_example"]


@pytest.mark.parametrize("result", [{"error": "Docker is not available"}, {}, {"returncode": 1}])
def test_report_does_not_claim_success_without_successful_execution(tmp_path, result):
    ctx = ToolContext(tmp_path, tmp_path, context={"last_tool_result": result})
    report = _render_report(ctx, "Report", "", True)
    assert "All tests passed" not in report
    assert "successfully" not in report
    assert "failed" in report or "inconclusive" in report


def test_report_uses_public_url_but_sends_to_internal_service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mock_effects_url", "http://mock_effects:8001")
    monkeypatch.setattr(settings, "mock_effects_public_url", "http://127.0.0.1:18011/")
    post = Mock(
        return_value=httpx.Response(
            200,
            json={"id": "key"},
            request=httpx.Request("POST", "http://mock_effects:8001/reports"),
        )
    )
    monkeypatch.setattr("app.tools.httpx.post", post)
    result = report_publish(ToolContext(tmp_path, tmp_path, idempotency_key="key"), "report.md")
    assert post.call_args.args[0] == "http://mock_effects:8001/reports"
    assert result["report_url"] == "http://127.0.0.1:18011/reports/key"


@pytest.mark.parametrize("database,allow", [("agentforge", "1"), ("agentforge_test", None)])
def test_destructive_fixtures_refuse_unsafe_database(database, allow):
    with pytest.raises(RuntimeError, match="Destructive tests require"):
        require_test_database(f"postgresql://localhost/{database}", allow)


def test_destructive_fixtures_accept_explicit_test_database():
    require_test_database("postgresql://localhost/agentforge_test", "1")
