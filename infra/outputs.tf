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

output "ecs_task_execution_role_arn" {
  description = "IAM role used by ECS tasks to pull images and publish logs."
  value       = aws_iam_role.ecs_task_execution.arn
}

output "ecs_express_infrastructure_role_arn" {
  description = "IAM role used by ECS Express Mode to manage load balancers, security groups, and autoscaling."
  value       = aws_iam_role.ecs_express_infrastructure.arn
}

output "ecs_express_service_arn" {
  description = "ARN of the Terraform-managed ECS Express service."
  value       = try(aws_cloudformation_stack.ecs_express_service[0].outputs["ServiceArn"], null)
}

output "service_url" {
  description = "Public ECS Express service URL."
  value       = try(trimspace(data.external.ecs_express_service_endpoint[0].result.endpoint) != "" ? data.external.ecs_express_service_endpoint[0].result.endpoint : null, null)
}
