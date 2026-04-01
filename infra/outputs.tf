output "aws_account_id" {
  description = "AWS account ID where the template infrastructure was provisioned."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "AWS region used by the template."
  value       = var.aws_region
}

output "ecr_repository_url" {
  description = "Full ECR repository URL for container pushes."
  value       = aws_ecr_repository.images.repository_url
}

output "github_actions_role_arn" {
  description = "IAM role assumed by GitHub Actions through OIDC."
  value       = aws_iam_role.github_actions.arn
}

output "app_runner_ecr_access_role_arn" {
  description = "IAM role used by App Runner to pull images from ECR."
  value       = aws_iam_role.app_runner_ecr_access.arn
}

output "app_runner_service_arn" {
  description = "ARN of the Terraform-managed App Runner service."
  value       = try(aws_apprunner_service.service[0].arn, null)
}

output "service_url" {
  description = "Public App Runner service URL."
  value       = try(format("https://%s", aws_apprunner_service.service[0].service_url), null)
}
