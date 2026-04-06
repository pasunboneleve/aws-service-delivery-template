data "aws_caller_identity" "current" {}

locals {
  apprunner_image_identifier = "${aws_ecr_repository.images.repository_url}:${var.apprunner_image_tag}"
}

data "external" "apprunner_image_presence" {
  program = [
    "bash",
    "${path.module}/../scripts/check-ecr-image.sh",
  ]

  query = {
    repository_name = var.service_name
    image_tag      = var.apprunner_image_tag
    aws_region     = var.aws_region
  }
}

data "aws_iam_policy_document" "github_oidc_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
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
    sid = "ManageAppRunner"
    actions = [
      "apprunner:DescribeService",
      "apprunner:ListServices",
      "apprunner:TagResource",
      "apprunner:UntagResource",
      "apprunner:UpdateService",
    ]
    resources = ["*"]
  }

  statement {
    sid = "PassAppRunnerAccessRole"
    actions = [
      "iam:PassRole",
    ]
    resources = [aws_iam_role.app_runner_ecr_access.arn]
  }
}

data "aws_iam_policy_document" "app_runner_ecr_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

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

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_iam_role" "app_runner_ecr_access" {
  name               = "${var.service_name}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.app_runner_ecr_assume_role.json
}

resource "aws_iam_role_policy_attachment" "app_runner_ecr_access" {
  role       = aws_iam_role.app_runner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_apprunner_service" "service" {
  count        = data.external.apprunner_image_presence.result.exists == "true" ? 1 : 0
  service_name = var.service_name

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.app_runner_ecr_access.arn
    }

    image_repository {
      image_identifier      = local.apprunner_image_identifier
      image_repository_type = "ECR"

      image_configuration {
        port = tostring(var.image_port)
      }
    }
  }

  instance_configuration {
    cpu    = var.apprunner_cpu
    memory = var.apprunner_memory
  }

  lifecycle {
    ignore_changes = [source_configuration[0].image_repository[0].image_identifier]
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
