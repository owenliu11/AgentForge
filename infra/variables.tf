variable "region" {
  type    = string
  default = "us-east-2"
}

variable "name" {
  type    = string
  default = "agentforge"
}

variable "allowed_cidr" {
  type        = string
  description = "The only address allowed to reach the API, normally your IP as x.x.x.x/32."
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "worker_count" {
  type    = number
  default = 3
}
