# app/tools.py
import inspect
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

MAX_READ_BYTES = 65_536
MAX_SEARCH_RESULTS = 20
MAX_OUTPUT_CHARS = 20_000
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class ToolContext:
    """Runtime inputs the loop supplies. The model never controls these."""

    repo_path: Path
    reports_dir: Path
    context: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


def _safe_path(base: Path, requested: str) -> Path:
    """
    Reject path traversal.

    The resolved requested path must remain inside base. resolve() follows
    symlinks, so a link pointing outside base is rejected too.
    """
    base = Path(base).resolve()
    candidate = (base / requested).resolve()

    if not candidate.is_relative_to(base):
        raise ToolError(f"Path rejected: {requested}")

    return candidate


def files_read(ctx: ToolContext, path: str) -> dict[str, Any]:
    target = _safe_path(ctx.repo_path, path)

    if not target.is_file():
        raise ToolError(f"File not found: {path}")

    with target.open("rb") as f:
        raw = f.read(MAX_READ_BYTES + 1)

    return {
        "path": path,
        "size": target.stat().st_size,
        "truncated": len(raw) > MAX_READ_BYTES,
        "content": raw[:MAX_READ_BYTES].decode("utf-8", errors="replace"),
    }


def files_search(ctx: ToolContext, pattern: str, path: str = ".") -> dict[str, Any]:
    repo_path = Path(ctx.repo_path).resolve()
    base = _safe_path(repo_path, path)

    if not base.exists():
        raise ToolError(f"Path not found: {path}")

    files = [base] if base.is_file() else sorted(base.rglob("*"))
    matches = []

    for file in files:
        if SKIP_DIRS.intersection(file.relative_to(repo_path).parts):
            continue

        # A symlinked file can point outside the repo; skip it.
        if not file.is_file() or not file.resolve().is_relative_to(repo_path):
            continue

        try:
            if file.stat().st_size > MAX_READ_BYTES:
                continue
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern in line:
                matches.append(
                    {
                        "path": str(file.relative_to(repo_path)),
                        "line": line_number,
                        "text": line.strip()[:200],
                    }
                )

                if len(matches) >= MAX_SEARCH_RESULTS:
                    return {"matches": matches, "truncated": True}

    return {"matches": matches, "truncated": False}


def _parse_unittest_failures(output: str) -> list[str]:
    """
    Parse unittest output for failed or errored tests.

    Example:
    FAIL: test_subtract_fails (tests.test_math.TestMath.test_subtract_fails)
    """
    failure_regex = re.compile(
        r"^(?:FAIL|ERROR):\s+(?P<short>\S+)\s*(?:\((?P<full>[^)]+)\))?",
        re.MULTILINE,
    )

    return [m.group("full") or m.group("short") for m in failure_regex.finditer(output)]


def tests_run(ctx: ToolContext, path: str = ".") -> dict[str, Any]:
    """
    Run the repo's unittest suite in a throwaway container.

    Always sandboxed: no network, repo mounted read-only. There is no
    host fallback, because the repo's code is untrusted.
    """
    repo = Path(ctx.repo_path).resolve()
    start_dir = _safe_path(repo, path)

    if not start_dir.is_dir():
        raise ToolError(f"Test path not found: {path}")

    container_dir = (Path("/repo") / start_dir.relative_to(repo)).as_posix()
    container_name = f"agentforge-tests-{uuid.uuid4().hex[:12]}"

    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--network", "none",
        "-v", f"{repo}:/repo:ro",
        "-w", "/repo",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        settings.sandbox_image,
        "python", "-m", "unittest", "discover",
        "-s", container_dir,
        "-t", "/repo",
    ]  # fmt: skip

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.tests_run_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ToolError("Docker is not available") from exc
    except subprocess.TimeoutExpired as exc:
        # Killing the docker CLI does not stop the container.
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        raise ToolError("tests.run timed out") from exc

    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"

    return {
        "returncode": proc.returncode,
        "failed_tests": _parse_unittest_failures(output),
        "output": output[-MAX_OUTPUT_CHARS:],
    }


def _render_report(ctx: ToolContext, title: str, content: str, include_last_result: bool) -> str:
    lines = [f"# {title}", ""]

    if content:
        lines.append(content[:1_000_000])
        lines.append("")

    if include_last_result:
        last_result = ctx.context.get("last_tool_result") or {}
        failed_tests = last_result.get("failed_tests") or []

        if failed_tests:
            lines.append("## Failing tests")
            lines.extend([f"- {test_name}" for test_name in failed_tests])
        else:
            lines.append("All tests passed.")

        lines.append("")

    return "\n".join(lines)


def report_publish(
    ctx: ToolContext,
    path: str,
    title: str = "AgentForge Report",
    content: str = "",
    include_last_result: bool = False,
) -> dict[str, Any]:
    """
    The effectful tool: publish a report to the downstream service.

    The request carries the call's idempotency key, and the downstream stores
    one report per key, so re-running this after a crash can't publish twice.
    """
    # The report name is still model-controlled, so keep it a contained path.
    _safe_path(ctx.reports_dir, path)

    if not ctx.idempotency_key:
        raise ToolError("report.publish requires an idempotency key")

    body = _render_report(ctx, title, content, include_last_result)
    base = settings.mock_effects_url.rstrip("/")

    response = httpx.post(
        f"{base}/reports",
        json={"path": path, "title": title, "body": body},
        headers={"Idempotency-Key": ctx.idempotency_key},
        timeout=10,
    )
    response.raise_for_status()
    report_id = response.json()["id"]

    return {
        "report_id": report_id,
        "report_url": f"{base}/reports/{report_id}",
        "path": path,
        "bytes": len(body.encode("utf-8")),
    }


def notify_post(ctx: ToolContext, message: str) -> dict[str, Any]:
    """
    An effect the downstream can't deduplicate (no idempotency key).

    If a worker dies after sending but before saving the result, nobody knows
    whether it was sent, so recovery fails the run with UNKNOWN_EFFECT.
    """
    base = settings.mock_effects_url.rstrip("/")
    response = httpx.post(f"{base}/notify", json={"message": message[:2_000]}, timeout=10)
    response.raise_for_status()
    return {"delivered": True}


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "files.read": files_read,
    "files.search": files_search,
    "tests.run": tests_run,
    "report.publish": report_publish,
    "notify.post": notify_post,
}

# How recovery treats an intent with no saved result:
#   none    - no side effects; just run it again
#   keyed   - side effect deduplicated downstream by idempotency key; re-run
#   unkeyed - side effect can't be deduplicated; fail with UNKNOWN_EFFECT
TOOL_EFFECTS: dict[str, str] = {
    "files.read": "none",
    "files.search": "none",
    "tests.run": "none",
    "report.publish": "keyed",
    "notify.post": "unkeyed",
}


def _allowed_args(fn: Callable[..., Any]) -> set[str]:
    params = list(inspect.signature(fn).parameters.values())[1:]  # skip ctx
    return {p.name for p in params}


def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if name not in TOOL_REGISTRY:
        raise ToolError(f"Unknown tool: {name}")

    fn = TOOL_REGISTRY[name]
    unknown = set(args) - _allowed_args(fn)

    if unknown:
        raise ToolError(f"Unknown args for {name}: {sorted(unknown)}")

    try:
        inspect.signature(fn).bind(ctx, **args)
    except TypeError as exc:
        raise ToolError(f"Bad args for {name}: {exc}") from exc

    return fn(ctx, **args)
