module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name                   = "agentflow-${var.env}"
  cluster_version                = "1.31"
  vpc_id                         = module.vpc.vpc_id
  subnet_ids                     = module.vpc.private_subnets
  cluster_endpoint_public_access = true
  enable_irsa                    = true

  enable_cluster_creator_admin_permissions = true

  eks_managed_node_groups = {
    # Workers clone repos and run test suites: CPU-heavy, bursty.
    default = {
      instance_types = ["m7g.large"]
      ami_type       = "AL2023_ARM_64_STANDARD"
      min_size       = 2
      max_size       = 6
      desired_size   = 2
    }
  }
}

# Pod role for the app's service account (Secrets Manager read via External Secrets).
module "irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.46"

  role_name = "agentflow-${var.env}-app"
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["agentflow:agentflow"]
    }
  }
  role_policy_arns = { secrets = aws_iam_policy.read_secrets.arn }
}

resource "aws_iam_policy" "read_secrets" {
  name = "agentflow-${var.env}-read-secrets"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}
