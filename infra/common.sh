# Shared by the infra scripts. Sourced, not run.
set -euo pipefail

export AWS_REGION="${AWS_REGION:-us-east-2}"
export AWS_PAGER=""
export TF_VAR_region="$AWS_REGION"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA="$ROOT/infra"

# Refuse to continue without AWS credentials, before anything else happens.
aws sts get-caller-identity --query Arn --output text >/dev/null

state_bucket() {
  terraform -chdir="$INFRA/bootstrap" output -raw state_bucket
}

init_backend() {
  terraform -chdir="$INFRA" init -input=false -reconfigure \
    -backend-config="bucket=$(state_bucket)" \
    -backend-config="key=agentforge/terraform.tfstate" \
    -backend-config="region=$AWS_REGION" \
    -backend-config="use_lockfile=true" >/dev/null
}

tf_output() {
  terraform -chdir="$INFRA" output -raw "$1"
}

# The first public IP of the ECS host.
host_ip() {
  terraform -chdir="$INFRA" output -json host_public_ips | jq -r '.[0] // empty'
}
