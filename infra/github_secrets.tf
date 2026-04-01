resource "github_actions_secret" "aws_role_to_assume" {
  repository      = var.github_repo
  secret_name     = "AWS_ROLE_TO_ASSUME"
  plaintext_value = aws_iam_role.github_actions.arn
}

resource "github_actions_secret" "aws_app_runner_ecr_access_role_arn" {
  repository      = var.github_repo
  secret_name     = "AWS_APP_RUNNER_ECR_ACCESS_ROLE_ARN"
  plaintext_value = aws_iam_role.app_runner_ecr_access.arn
}

resource "github_actions_variable" "aws_region" {
  repository    = var.github_repo
  variable_name = "AWS_REGION"
  value         = var.aws_region
}

resource "github_actions_variable" "aws_ecr_repository" {
  repository    = var.github_repo
  variable_name = "AWS_ECR_REPOSITORY"
  value         = aws_ecr_repository.images.name
}

resource "github_actions_variable" "aws_app_runner_service_name" {
  repository    = var.github_repo
  variable_name = "AWS_APP_RUNNER_SERVICE_NAME"
  value         = var.service_name
}

resource "github_actions_variable" "aws_app_runner_port" {
  repository    = var.github_repo
  variable_name = "AWS_APP_RUNNER_PORT"
  value         = tostring(var.image_port)
}
