resource "aws_db_subnet_group" "main" {
  name_prefix = "${var.name}-"
  subnet_ids  = module.vpc.private_subnets
}

resource "aws_db_instance" "main" {
  identifier_prefix = "${var.name}-"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = var.db_instance_class
  storage_type      = "gp3"
  allocated_storage = 20
  storage_encrypted = true

  db_name  = var.name
  username = var.name
  # RDS generates the password and keeps it in Secrets Manager.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  # A demo database that `make destroy` removes after every session.
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true
}
