# Optional AWS demonstration

**Recommendation for TestForge:** finish the isolated local target and GitHub Actions
acceptance workflow first. AWS is a separate environment adapter and recovery demonstration,
not a prerequisite for AgentForge or TestForge. No cloud deployment was performed as part
of the current release preparation.

## Existing architecture

| Component | Implementation |
|---|---|
| ECS on EC2 | One host; API, workers and a separate mock-effects service |
| PostgreSQL | Private RDS instance; database credentials injected from Secrets Manager |
| Images and logs | ECR image and CloudWatch log group |
| Network | Public host, private RDS subnets, ingress restricted to `allowed_cidr` |
| State/bootstrap | Separate Terraform state bucket, budget notification and optional OIDC roles |

Keep ECS on EC2 for the present worker design: `tests.run` launches sibling containers
through the host Docker socket and requires host filesystem mounts. Moving this worker
to Fargate would require redesigning the sandbox execution boundary. Fargate also requires
`awsvpc` rather than this stack's host networking. See [AWS Fargate task differences](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)
and [ECS bind mounts](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html).

## What to keep now

- Keep Terraform, the deployment scripts and the cloud drill as optional portfolio code.
- Require formatting, validation and mocked-provider tests in CI. These do not create cloud
  resources, though initialization downloads providers/modules.
- Keep ordinary CI credential-free. Account-connected plans are not required for pull requests.
- Use local deterministic FakeModel scenarios for TestForge. A real model is a separate,
  potentially nondeterministic integration and is not needed to demonstrate recovery.

## Before running the cloud demonstration

Use an AWS identity and region permitted by your actual account. The default region is
`us-east-2`; it is a project default, not a universal AWS free-plan restriction. OIDC is off
by default and requires account permissions when enabled. There is no verified hourly cost
estimate in this guide. Estimate EC2, RDS, public IPv4, storage, ECR and log costs for the
chosen region before deployment. Budget notifications do not stop resources automatically.

Prerequisites: AWS CLI authentication, Docker/buildx, Terraform >=1.10 and jq.
Confirm the intended account with `aws sts get-caller-identity`. Bootstrap creates resources
that outlive the application stack:

```bash
BUDGET_EMAIL=your-real-address make bootstrap
# Optional OIDC, only when permitted:
# ENABLE_GITHUB_OIDC=true BUDGET_EMAIL=your-real-address make bootstrap
```

Keep the local bootstrap state private and backed up. State, credentials, plan outputs and
`.tfvars` are ignored by Git. Provider lockfiles are intended to be committed.

```bash
make deploy       # billable: builds/pushes an image and applies infrastructure
make cloud-drill  # controlled worker-task failure against this demo stack
make destroy      # deletes the application stack, including its disposable RDS data
```

The scripts apply changes automatically; inspect the Terraform and target account first.
`make deploy` runs a one-off migration before forcing service redeployments. The existing
GitHub workflow is a manually dispatched **image-only rollout**, restricted to main when
`AWS_DEPLOY_ROLE_ARN` exists. It does not create the stack or run migrations. Use the full
local deployment script for schema/infrastructure changes; the workflow is not a complete
production release pipeline and does not prove service health after submission.

## Limitations to explain in a demo

- One host is a failure domain. The drill demonstrates worker-task recovery, not availability
  through host or RDS loss.
- Docker-socket access is host-privileged. Do not treat this as hardened multi-tenant execution.
- The API is unauthenticated HTTP behind an IP restriction; it is not a public production API.
- Mock effects keep reports/idempotency state in memory. Restarting that service loses it.
- Images currently deploy through mutable `latest`. Before using cloud runs as reproducible
  TestForge release evidence, pin immutable image digests and record the task definitions.
- RDS backups/final snapshots are disabled for disposable demonstrations. Teardown deletes data.
- Bootstrap resources and logs/storage may remain after application teardown. Check the actual
  resource inventory and billing; a tag query alone is not proof of zero remaining charges.

## Next AWS milestone, after TestForge works locally

Add one explicit cloud acceptance job: provision a dedicated environment, run a small subset
of the same contract/recovery checks, retain logs and resource/image identifiers, and tear
it down. Prefer an ephemeral runner able to reach the private environment. Keep routine
functional tests in local CI. Introduce immutable deployments, migration gating, bounded
readiness and cleanup verification before calling that cloud workflow complete.
