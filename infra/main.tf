data "aws_caller_identity" "current" {}

moved {
  from = aws_iam_openid_connect_provider.github
  to   = aws_iam_openid_connect_provider.github[0]
}

locals {
  ecs_express_image_identifier = "${aws_ecr_repository.images.repository_url}:${var.ecs_express_image_tag}"
  # Use a placeholder ARN if the provider is not managed and not provided to avoid plan errors.
  # The placeholder must be a valid ARN format for IAM policy validation.
  github_oidc_provider_arn = var.manage_github_oidc_provider ? try(one(aws_iam_openid_connect_provider.github[*].arn), "") : (var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : "")
  ecs_express_stack_template = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Parameters = {
      ImageUri = {
        Type = "String"
      }
    }
    Resources = {
      ExpressService = {
        Type = "AWS::ECS::ExpressGatewayService"
        Properties = {
          ServiceName           = var.service_name
          ExecutionRoleArn      = aws_iam_role.ecs_task_execution.arn
          InfrastructureRoleArn = aws_iam_role.ecs_express_infrastructure.arn
          Cpu                   = var.ecs_express_cpu
          Memory                = var.ecs_express_memory
          HealthCheckPath       = var.health_check_path
          PrimaryContainer = {
            Image = {
              Ref = "ImageUri"
            }
            ContainerPort = var.image_port
          }
          ScalingTarget = {
            MinTaskCount           = var.ecs_express_min_task_count
            MaxTaskCount           = var.ecs_express_max_task_count
            AutoScalingMetric      = var.ecs_express_scaling_metric
            AutoScalingTargetValue = var.ecs_express_scaling_target_value
          }
        }
      }
    }
    Outputs = {
      ServiceArn = {
        Value = {
          Ref = "ExpressService"
        }
      }
    }
  })
}

data "external" "image_presence" {
  program = [
    "bash",
    "${path.module}/../scripts/check-ecr-image.sh",
  ]

  query = {
    repository_name = var.service_name
    image_tag       = var.ecs_express_image_tag
    aws_region      = var.aws_region
  }
}

data "aws_iam_policy_document" "github_oidc_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = [var.github_oidc_audience]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = var.github_environment != "" ? [
        "repo:${var.github_owner}/${var.github_repo}:environment:${var.github_environment}"
        ] : [
        "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/${var.github_branch}"
      ]
    }
  }
}

data "aws_iam_policy_document" "github_actions_permissions" {
  statement {
    sid = "EcrPushPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetAuthorizationToken",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ManageEcsExpressService"
    actions = [
      "ecs:DescribeExpressGatewayService",
      "ecs:UpdateExpressGatewayService",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "ecs_task_execution_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ecs_express_infrastructure_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs.amazonaws.com"]
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.manage_github_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"

  client_id_list = [
    var.github_oidc_audience,
  ]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
  ]
}

resource "aws_ecr_repository" "images" {
  name                 = var.service_name
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.service_name}-ecs-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_execution_assume_role.json
}

resource "terraform_data" "ensure_ecs_service_linked_role" {
  provisioner "local-exec" {
    command = "bash ${path.module}/../scripts/ensure-ecs-service-linked-role.sh"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_express_infrastructure" {
  name               = "${var.service_name}-ecs-express-infra"
  assume_role_policy = data.aws_iam_policy_document.ecs_express_infrastructure_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_express_infrastructure" {
  role       = aws_iam_role.ecs_express_infrastructure.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices"
}

resource "aws_cloudformation_stack" "ecs_express_service" {
  count = data.external.image_presence.result.exists == "true" ? 1 : 0
  name  = var.service_name

  template_body = local.ecs_express_stack_template
  parameters = {
    ImageUri = local.ecs_express_image_identifier
  }

  lifecycle {
    ignore_changes = [parameters["ImageUri"]]
  }

  depends_on = [
    terraform_data.ensure_ecs_service_linked_role,
  ]
}

data "external" "ecs_express_service_endpoint" {
  count = length(aws_cloudformation_stack.ecs_express_service) == 1 ? 1 : 0
  program = [
    "bash",
    "${path.module}/../scripts/describe-ecs-express-service.sh",
  ]

  query = {
    service_arn = aws_cloudformation_stack.ecs_express_service[0].outputs["ServiceArn"]
    aws_region  = var.aws_region
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.github_repo}-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_oidc_assume_role.json
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "${var.github_repo}-github-actions-deploy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_permissions.json
}
