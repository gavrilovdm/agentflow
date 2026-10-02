# Postgres 17 on RDS ships the pgvector extension: one database serves LangGraph
# checkpoints, the long-term memory Store and the code-chunk vector index.
resource "random_password" "db" {
  length  = 32
  special = false
}

module "db" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.9"

  identifier                   = "agentflow-${var.env}"
  engine                       = "postgres"
  engine_version               = "17"
  family                       = "postgres17"
  major_engine_version         = "17"
  instance_class               = var.db_instance_class
  allocated_storage            = 50
  max_allocated_storage        = 200
  db_name                      = "agentflow"
  username                     = "agentflow"
  password                     = random_password.db.result
  manage_master_user_password  = false
  port                         = 5432
  multi_az                     = var.env == "prod"
  db_subnet_group_name         = module.vpc.database_subnet_group_name
  vpc_security_group_ids       = [aws_security_group.db.id]
  backup_retention_period      = 7
  deletion_protection          = var.env == "prod"
  performance_insights_enabled = true
}

resource "aws_security_group" "db" {
  name   = "agentflow-${var.env}-db"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "agentflow-${var.env}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name   = "agentflow-${var.env}-redis"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id         = "agentflow-${var.env}"
  engine             = "redis"
  node_type          = "cache.t4g.small"
  num_cache_nodes    = 1
  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]
}

# API keys are filled in by hand (or another pipeline); Terraform owns only the
# connection strings it knows.
resource "aws_secretsmanager_secret" "app" {
  name = "agentflow/${var.env}"
}

resource "aws_secretsmanager_secret_version" "connections" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL = "postgresql://agentflow:${random_password.db.result}@${module.db.db_instance_address}:5432/agentflow"
    REDIS_URL    = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379"
  })
  lifecycle { ignore_changes = [secret_string] }
}
