data "aws_availability_zones" "available" {}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name               = "agentflow-${var.env}"
  cidr               = "10.40.0.0/16"
  azs                = slice(data.aws_availability_zones.available.names, 0, 3)
  private_subnets    = ["10.40.0.0/19", "10.40.32.0/19", "10.40.64.0/19"]
  public_subnets     = ["10.40.96.0/22", "10.40.100.0/22", "10.40.104.0/22"]
  database_subnets   = ["10.40.112.0/24", "10.40.113.0/24", "10.40.114.0/24"]
  enable_nat_gateway = true
  single_nat_gateway = var.env != "prod"

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}
