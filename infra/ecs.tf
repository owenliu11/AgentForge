resource "aws_ecs_cluster" "main" {
  name = var.name
}

data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

# One host. EC2 rather than Fargate because tests.run starts sibling containers
# through the host's Docker socket.
resource "aws_launch_template" "host" {
  name_prefix   = "${var.name}-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.host.arn
  }

  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.host.id]
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = base64encode(<<-SCRIPT
    #!/bin/bash
    echo "ECS_CLUSTER=${aws_ecs_cluster.main.name}" >> /etc/ecs/ecs.config
  SCRIPT
  )
}

resource "aws_autoscaling_group" "host" {
  name_prefix         = "${var.name}-"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = module.vpc.public_subnets
  force_delete        = true

  launch_template {
    id      = aws_launch_template.host.id
    version = aws_launch_template.host.latest_version
  }

  instance_refresh {
    strategy = "Rolling"
  }

  tag {
    key                 = "Name"
    value               = "${var.name}-host"
    propagate_at_launch = true
  }
}

locals {
  image = "${aws_ecr_repository.app.repository_url}:latest"

  # Fixture repos must exist on the host, because the Docker daemon resolves
  # tests.run's -v path there. Workers copy them out of the image at this path.
  repos_on_host = "/opt/${var.name}/repos"
  db_secret     = aws_db_instance.main.master_user_secret[0].secret_arn

  # Host networking: every task shares the one EC2 host, so workers reach
  # mock_effects on localhost. mock_effects is its own service, not a worker
  # sidecar: stopping a worker must not wipe the duplicate counters.
  shared_env = {
    DB_HOST          = aws_db_instance.main.address
    DB_PORT          = tostring(aws_db_instance.main.port)
    DB_NAME          = aws_db_instance.main.db_name
    MOCK_EFFECTS_URL = "http://localhost:8001"
    REPOS_ROOT       = "/srv/tests/fixtures"
    MODEL_SCRIPT     = "/srv/tests/fixtures/scripts/repo_analysis.yaml"
  }

  tasks = {
    api = {
      command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
      memory  = 256
      port    = 8000
      desired = 1
      env     = {}
      mounts  = {}
    }
    mock_effects = {
      command = ["uvicorn", "mock_effects.app:app", "--host", "0.0.0.0", "--port", "8001"]
      memory  = 128
      port    = 8001
      desired = 1
      env     = {}
      mounts  = {}
    }
    worker = {
      command = [
        "sh", "-c",
        "cp -r /srv/tests/fixtures/. ${local.repos_on_host}/ && exec python -m app.worker",
      ]
      memory  = 256
      port    = null
      desired = var.worker_count
      env = {
        REPOS_ROOT    = local.repos_on_host
        SANDBOX_IMAGE = "public.ecr.aws/docker/library/python:3.12-alpine"
      }
      mounts = {
        docker-socket = "/var/run/docker.sock"
        repos         = local.repos_on_host
      }
    }
    # Run once per deploy by infra/deploy.sh, so workers never race to migrate.
    migrate = {
      command = ["alembic", "upgrade", "head"]
      memory  = 256
      port    = null
      desired = 0
      env     = {}
      mounts  = {}
    }
  }
}

resource "aws_ecs_task_definition" "app" {
  for_each = local.tasks

  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["EC2"]
  network_mode             = "host"
  execution_role_arn       = aws_iam_role.task_execution.arn

  dynamic "volume" {
    for_each = each.value.mounts

    content {
      name      = volume.key
      host_path = volume.value
    }
  }

  container_definitions = jsonencode([{
    name      = each.key
    image     = local.image
    command   = each.value.command
    memory    = each.value.memory
    essential = true

    portMappings = each.value.port == null ? [] : [{
      containerPort = each.value.port
      hostPort      = each.value.port
    }]

    environment = [
      for key, value in merge(local.shared_env, each.value.env) : { name = key, value = value }
    ]

    secrets = [
      { name = "DB_USERNAME", valueFrom = "${local.db_secret}:username::" },
      { name = "DB_PASSWORD", valueFrom = "${local.db_secret}:password::" },
    ]

    mountPoints = [
      for name, path in each.value.mounts : { sourceVolume = name, containerPath = path }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = each.key
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  for_each = { for name, task in local.tasks : name => task if task.desired > 0 }

  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app[each.key].arn
  desired_count   = each.value.desired
  launch_type     = "EC2"
  force_delete    = true

  # One host and fixed ports: a task must stop before its replacement starts.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
}
