data "aws_availability_zones" "available" {
  state = "available"
}

# Public subnets for the ECS host, private for RDS. No NAT gateway: the host has
# a public IP and the database never needs to reach out.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = var.name
  cidr = "10.0.0.0/16"

  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets  = ["10.0.1.0/24", "10.0.2.0/24"]
  private_subnets = ["10.0.11.0/24", "10.0.12.0/24"]

  enable_nat_gateway      = false
  map_public_ip_on_launch = true
}

resource "aws_security_group" "host" {
  name_prefix = "${var.name}-host-"
  description = "ECS host: API and effect stats from one address only"
  vpc_id      = module.vpc.vpc_id

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "api" {
  for_each = { api = 8000, mock_effects = 8001 }

  security_group_id = aws_security_group.host.id
  description       = "${each.key} from the allowed address"
  cidr_ipv4         = var.allowed_cidr
  from_port         = each.value
  to_port           = each.value
  ip_protocol       = "tcp"
}

# Pulling images, reaching ECS, Secrets Manager, and CloudWatch.
resource "aws_vpc_security_group_egress_rule" "host_all" {
  security_group_id = aws_security_group.host.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "db" {
  name_prefix = "${var.name}-db-"
  description = "Postgres from the ECS host only"
  vpc_id      = module.vpc.vpc_id

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_host" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the ECS host"
  referenced_security_group_id = aws_security_group.host.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
