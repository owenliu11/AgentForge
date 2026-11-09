# Release preparation — 2026-11-09

AgentForge is prepared as a local application target for the separate TestForge platform.
This update was verified locally in an isolated copy with the committed dependency lockfile.
No Git commit, GitHub push or AWS deployment was performed. The GitHub Actions workflows
still need their first run on the committed revision.

## Verification performed

| Check | Result |
|---|---|
| Ruff lint and formatting | Passed |
| Unit/integration tests and real-process recovery drills | 47 passed |
| Rebuilt Docker target and external HTTP smoke | All 4 scenarios passed |
| Recovery measurement helper | Passed with 2 samples; a helper check, not a benchmark |
| Terraform formatting and both stack validations | Passed |
| Terraform mocked-provider test | 1 passed, 0 failed; no AWS resources created |
| Shell syntax and Compose/workflow YAML parsing | Passed |

The test run emitted one dependency deprecation warning from Starlette; it did not fail.
The smoke checks cover an end-to-end run/report (including the intentional fixture failure),
invalid repository rejection, a missing run, and a step-limit failure. The recovery drills
remain AgentForge's own tests; they are not evidence that TestForge itself is implemented.

The disposable verification databases and acceptance stack were removed after checking.
The existing development database was left running and unchanged by these fixtures.
Local evidence is retained under `reports/release-2026-11-09/` and is ignored by Git.
Image identifiers are recorded with the smoke evidence. The measurement helper writes new
results under `reports/` rather than overwriting the historical recovery sample.

## Reproduce before committing

```bash
make install
make verify
make testforge-up
make testforge-smoke
make testforge-down
make tf-check
```

The verification commands require a running Docker daemon. Terraform initialization needs
network access to download providers/modules; validation does not require AWS credentials.
Use `make testforge-down` even if a smoke check fails so the dedicated test volumes are removed.

## GitHub handoff

1. Review `git status --short` and `git diff`. Existing changes to the runtime and AWS files
   were preserved; include the intended files from that earlier work in your review.
2. Commit source, documentation, fixtures, workflows, `uv.lock` and Terraform provider
   lockfiles together. Review new files too: ordinary `git diff` does not show untracked files.
3. Keep `.env`, Terraform state/variables/plan files, caches and `reports/` out of the commit.
   The updated ignore rules cover these generated/private files.
4. Push the reviewed commit and require all three CI jobs (test, acceptance, terraform) to pass.
5. Pin that commit in the future TestForge target configuration. Keep the TestForge core
   independent of AgentForge internals; use the [HTTP contract](TESTFORGE.md).

Suggested commit subject: `Prepare AgentForge as an isolated TestForge target`

## AWS decision

Keep AWS optional for this milestone. The current Docker-socket worker fits the existing
ECS-on-EC2 design; using Fargate requires a different sandbox boundary. Complete reusable
local TestForge execution and artifacts first, then add an explicitly provisioned cloud
acceptance environment. See [the AWS guide](../infra/README.md) for the current deployment
limits and next milestone. Cloud readiness has not been verified by these local checks.
