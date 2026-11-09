# Evaluates the whole stack against a mocked AWS provider, so these run offline,
# with no account and no cost. Run with `make tf-check`.

mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-2a", "us-east-2b"] }
  }

  override_data {
    target = data.aws_ssm_parameter.ecs_ami
    values = { value = "ami-0123456789abcdef0" }
  }

  override_data {
    target = data.aws_iam_policy_document.task_execution
    values = { json = "{}" }
  }

  override_data {
    target = data.aws_iam_policy_document.ecs_tasks_trust
    values = { json = "{}" }
  }

  override_data {
    target = data.aws_iam_policy_document.ec2_trust
    values = { json = "{}" }
  }

  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/agentforge" }
  }

  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::123456789012:instance-profile/agentforge" }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-2:123456789012:log-group:/ecs/agentforge" }
  }

  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }

  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-2:123456789012:cluster/agentforge" }
  }

  override_resource {
    target = aws_db_instance.main
    values = {
      address            = "db.internal"
      port               = 5432
      db_name            = "agentforge"
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-2:123:secret:db" }]
    }
  }

  override_resource {
    target = aws_ecr_repository.app
    values = { repository_url = "123.dkr.ecr.us-east-2.amazonaws.com/agentforge" }
  }
}

variables {
  allowed_cidr = "203.0.113.7/32"
}

run "stack_matches_the_design" {
  command = apply

  assert {
    condition     = aws_ecs_service.app["worker"].desired_count == 3
    error_message = "Phase 4 runs three workers."
  }

  assert {
    condition     = aws_ecs_service.app["api"].desired_count == 1
    error_message = "Phase 4 runs one API task."
  }

  assert {
    condition     = contains(keys(aws_ecs_service.app), "mock_effects")
    error_message = "mock_effects must outlive a stopped worker, so it needs its own service."
  }

  assert {
    condition     = !contains(keys(aws_ecs_service.app), "migrate")
    error_message = "Migrations are a one-off task, never a service."
  }

  assert {
    condition     = aws_db_instance.main.publicly_accessible == false
    error_message = "RDS must stay private."
  }

  assert {
    condition     = aws_db_instance.main.manage_master_user_password == true
    error_message = "The database password must live in Secrets Manager."
  }

  assert {
    condition     = alltrue([for rule in aws_vpc_security_group_ingress_rule.api : rule.cidr_ipv4 == var.allowed_cidr])
    error_message = "The host must only accept traffic from the allowed address."
  }

  assert {
    condition = alltrue([
      for name in ["api", "mock_effects", "worker", "migrate"] :
      aws_ecs_task_definition.app[name].network_mode == "host"
    ])
    error_message = "Tasks share the host network so workers reach mock_effects on localhost."
  }

  assert {
    condition = (
      length(aws_ecs_task_definition.app["worker"].volume) == 2
      && length(aws_ecs_task_definition.app["api"].volume) == 0
    )
    error_message = "Only workers get the Docker socket and the repos directory."
  }

  assert {
    condition = alltrue([
      for secret in jsondecode(aws_ecs_task_definition.app["worker"].container_definitions)[0].secrets :
      endswith(secret.valueFrom, "::")
    ])
    error_message = "Secrets must select a JSON key from the RDS secret."
  }
}
