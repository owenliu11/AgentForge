#!/usr/bin/env bash
# Create the optional AWS foundations. Run once. Needs BUDGET_EMAIL.
source "$(dirname "$0")/common.sh"

: "${BUDGET_EMAIL:?Set BUDGET_EMAIL to the address for the budget alarm}"

case "$BUDGET_EMAIL" in
  *@example.com)
    echo "BUDGET_EMAIL is still the placeholder. Use your real email address." >&2
    exit 1
    ;;
esac

terraform -chdir="$INFRA/bootstrap" init -input=false
terraform -chdir="$INFRA/bootstrap" apply \
  -var "budget_email=$BUDGET_EMAIL" \
  -var "enable_github_oidc=${ENABLE_GITHUB_OIDC:-false}"

# Terraform omits null outputs, so read them all rather than asking for this one.
DEPLOY_ROLE="$(terraform -chdir="$INFRA/bootstrap" output -json | jq -r '.deploy_role_arn.value // empty')"

if [ -z "$DEPLOY_ROLE" ]; then
  echo
  echo "Done. GitHub OIDC is off, so deploy with make deploy from this machine."
  exit 0
fi

cat <<OUT

Add these as GitHub repository variables (Settings > Secrets and variables > Actions > Variables):
  AWS_DEPLOY_ROLE_ARN = $DEPLOY_ROLE
  AWS_PLAN_ROLE_ARN   = $(terraform -chdir="$INFRA/bootstrap" output -raw plan_role_arn)
  TF_STATE_BUCKET     = $(state_bucket)
  AWS_REGION          = $AWS_REGION
OUT
