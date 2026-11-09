# Optional foundations that outlive `make destroy`: the Terraform state bucket, the
# budget alarm, and the roles GitHub Actions signs in to with OIDC. Applied once,
# with local state, by `make bootstrap`.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "agentforge"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "github_repo" {
  type        = string
  description = "The owner/name allowed to sign in from GitHub Actions."
  default     = "owenliu11/AgentForge"
}

variable "enable_github_oidc" {
  type        = bool
  description = "Create optional GitHub OIDC roles when account permissions allow it. Disabled by default."
  default     = false
}

variable "budget_email" {
  type        = string
  description = "Where AWS Budgets sends the $20 alarm."
}

data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  oidc_subject = "token.actions.githubusercontent.com:sub"
}

resource "aws_s3_bucket" "state" {
  bucket = "agentforge-tfstate-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_budgets_budget" "monthly" {
  name         = "agentforge-monthly"
  budget_type  = "COST"
  limit_amount = "20"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.enable_github_oidc ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "deploy_trust" {
  count = var.enable_github_oidc ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Only workflows running on main can assume the deploy role.
    condition {
      test     = "StringEquals"
      variable = local.oidc_subject
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

data "aws_iam_policy_document" "plan_trust" {
  count = var.enable_github_oidc ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = local.oidc_subject
      values = [
        "repo:${var.github_repo}:pull_request",
        "repo:${var.github_repo}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "deploy" {
  count = var.enable_github_oidc ? 1 : 0

  name               = "agentforge-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust[0].json
}

data "aws_iam_policy_document" "deploy" {
  count = var.enable_github_oidc ? 1 : 0

  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["arn:aws:ecr:${var.region}:${local.account_id}:repository/agentforge"]
  }

  statement {
    actions   = ["ecs:UpdateService"]
    resources = ["arn:aws:ecs:${var.region}:${local.account_id}:service/agentforge/*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  count = var.enable_github_oidc ? 1 : 0

  role   = aws_iam_role.deploy[0].id
  policy = data.aws_iam_policy_document.deploy[0].json
}

# `terraform plan` on pull requests only needs to read.
resource "aws_iam_role" "plan" {
  count = var.enable_github_oidc ? 1 : 0

  name               = "agentforge-github-plan"
  assume_role_policy = data.aws_iam_policy_document.plan_trust[0].json
}

resource "aws_iam_role_policy_attachment" "plan" {
  count = var.enable_github_oidc ? 1 : 0

  role       = aws_iam_role.plan[0].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "deploy_role_arn" {
  value = one(aws_iam_role.deploy[*].arn)
}

output "plan_role_arn" {
  value = one(aws_iam_role.plan[*].arn)
}
