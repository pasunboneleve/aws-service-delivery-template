data "aws_caller_identity" "current" {}

locals {
  service_name = var.service_name
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
      "apprunner:CreateService",
      "apprunner:DescribeService",
      "apprunner:ListServices",
      "apprunner:StartDeployment",
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

  statement {
    sid = "CreateAppRunnerServiceLinkedRole"
    actions = [
      "iam:CreateServiceLinkedRole",
    ]
    resources = ["*"]

    condition {
      test     = "StringLike"
      variable = "iam:AWSServiceName"
      values   = ["apprunner.amazonaws.com"]
    }
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
  name                 = local.service_name
  image_tag_mutability = var.image_tag_mutability

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_iam_role" "app_runner_ecr_access" {
  name               = "${local.service_name}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.app_runner_ecr_assume_role.json
}

resource "aws_iam_role_policy_attachment" "app_runner_ecr_access" {
  role       = aws_iam_role.app_runner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
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
