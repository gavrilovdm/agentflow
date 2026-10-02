output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
output "cluster_name" { value = module.eks.cluster_name }
output "irsa_role_arn" { value = module.irsa.iam_role_arn }
output "ci_role_arn" { value = aws_iam_role.ci.arn }
output "secret_name" { value = aws_secretsmanager_secret.app.name }
