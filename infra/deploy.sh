#!/usr/bin/env bash
# Build AgentForge in AWS from a clean checkout. This starts billable resources.
source "$(dirname "$0")/common.sh"

export TF_VAR_allowed_cidr="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')/32"
echo "API access limited to $TF_VAR_allowed_cidr"

init_backend

echo "==> Creating the image repository"
terraform -chdir="$INFRA" apply -input=false -auto-approve -target=aws_ecr_repository.app

REPOSITORY="$(tf_output ecr_repository)"

echo "==> Building and pushing the image"
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPOSITORY%%/*}"
docker buildx build --platform linux/amd64 --tag "$REPOSITORY:latest" --push "$ROOT"

echo "==> Creating the infrastructure (RDS takes about 5 minutes)"
terraform -chdir="$INFRA" apply -input=false -auto-approve

CLUSTER="$(tf_output cluster)"

echo "==> Waiting for the ECS host to join the cluster"
for _ in $(seq 1 60); do
  COUNT="$(aws ecs describe-clusters --clusters "$CLUSTER" \
    --query 'clusters[0].registeredContainerInstancesCount' --output text)"
  [ "$COUNT" -ge 1 ] && break
  sleep 5
done
[ "$COUNT" -ge 1 ] || { echo "The ECS host never joined the cluster" >&2; exit 1; }

echo "==> Running migrations"
TASK="$(aws ecs run-task --cluster "$CLUSTER" --launch-type EC2 \
  --task-definition "$(tf_output migrate_task_definition)" \
  --query 'tasks[0].taskArn' --output text)"
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
CODE="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
[ "$CODE" = "0" ] || { echo "Migration failed with exit code $CODE" >&2; exit 1; }

echo "==> Rolling out the image"
for SERVICE in api mock_effects worker; do
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --force-new-deployment --query 'service.serviceName' --output text
done
aws ecs wait services-stable --cluster "$CLUSTER" --services api mock_effects worker

terraform -chdir="$INFRA" apply -input=false -auto-approve -refresh-only >/dev/null

IP="$(host_ip)"
echo
echo "AgentForge is up: http://$IP:8000/health"
echo "Run the drill with: make cloud-drill"
echo "This costs money while it runs. Finish with: make destroy"
