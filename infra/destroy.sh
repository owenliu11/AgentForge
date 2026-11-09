#!/usr/bin/env bash
# Tear down everything `make deploy` built. `make bootstrap`'s resources stay.
source "$(dirname "$0")/common.sh"

# Any valid value works: the rule is being deleted.
export TF_VAR_allowed_cidr="203.0.113.0/32"

init_backend
terraform -chdir="$INFRA" destroy -input=false -auto-approve

echo "Destroyed. Check no billable resources remain with:"
echo "  aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=agentforge"
