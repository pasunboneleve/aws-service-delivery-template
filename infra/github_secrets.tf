resource "github_actions_secret" "aws_role_to_assume" {
  repository      = var.github_repo
  secret_name     = "AWS_ROLE_TO_ASSUME"
  plaintext_value = aws_iam_role.github_actions.arn
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

resource "github_actions_variable" "aws_ecs_express_service_arn" {
  count         = length(aws_cloudformation_stack.ecs_express_service) == 1 ? 1 : 0
  repository    = var.github_repo
  variable_name = "AWS_ECS_EXPRESS_SERVICE_ARN"
  value         = aws_cloudformation_stack.ecs_express_service[0].outputs["ServiceArn"]
}

resource "github_actions_variable" "aws_ecs_container_port" {
  repository    = var.github_repo
  variable_name = "AWS_ECS_CONTAINER_PORT"
  value         = tostring(var.image_port)
}
