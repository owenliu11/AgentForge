data "aws_instances" "host" {
  instance_tags = {
    Name = "${var.name}-host"
  }
  instance_state_names = ["running"]

  depends_on = [aws_autoscaling_group.host]
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecr_repository" {
  value = aws_ecr_repository.app.repository_url
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.app["migrate"].family
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}

output "host_public_ips" {
  description = "Empty until the host has started; `make deploy` refreshes it."
  value       = data.aws_instances.host.public_ips
}
