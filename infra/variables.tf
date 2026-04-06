variable "aws_region" {
  description = "AWS region used for ECR, ECS Express Mode, and Terraform state."
  type        = string
}

variable "service_name" {
  description = "Base service name used for the ECR repository and ECS Express service."
  type        = string
}

variable "github_owner" {
  description = "GitHub organization or user."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
}

variable "github_branch" {
  description = "Git ref allowed to assume the GitHub Actions deploy role."
  type        = string
  default     = "main"
}

variable "github_oidc_audience" {
  description = "Audience used by GitHub's OIDC token when assuming the AWS role."
  type        = string
  default     = "sts.amazonaws.com"
}

variable "manage_github_oidc_provider" {
  description = "Whether Terraform should create and manage the GitHub Actions OIDC provider in this stack."
  type        = bool
  default     = true
}

variable "github_oidc_provider_arn" {
  description = "Existing GitHub Actions OIDC provider ARN to reuse when manage_github_oidc_provider is false."
  type        = string
  default     = null

  validation {
    condition     = var.manage_github_oidc_provider || (var.github_oidc_provider_arn != null && trimspace(var.github_oidc_provider_arn) != "")
    error_message = "Set github_oidc_provider_arn when manage_github_oidc_provider is false."
  }
}

variable "github_environment" {
  description = "Optional GitHub Actions environment name to constrain the OIDC trust policy."
  type        = string
  default     = ""
}

variable "github_repo_visibility" {
  description = "Metadata tag only. Useful when publishing the template."
  type        = string
  default     = "private"
}

variable "github_token" {
  description = "Optional GitHub Personal Access Token with repo scope for managing Actions secrets and variables."
  type        = string
  sensitive   = true
  default     = null
}

variable "image_port" {
  description = "Container port exposed by the application."
  type        = number
  default     = 8080
}

variable "image_tag_mutability" {
  description = "ECR tag mutability setting."
  type        = string
  default     = "MUTABLE"
}

variable "ecr_force_delete" {
  description = "Whether Terraform should force-delete non-empty ECR repositories. Keep false for long-lived stacks; integration runs set this to true."
  type        = bool
  default     = false
}

variable "ecs_express_cpu" {
  description = "CPU units for the ECS Express service task. Default 256 = 0.25 vCPU."
  type        = string
  default     = "256"
}

variable "ecs_express_memory" {
  description = "Memory for the ECS Express service task in MiB. Default 512 = 0.5 GB RAM."
  type        = string
  default     = "512"
}

variable "ecs_express_image_tag" {
  description = "Container image tag Terraform should use when creating or recreating the ECS Express service."
  type        = string
  default     = "latest"
}

variable "health_check_path" {
  description = "Health check path for the ECS Express service."
  type        = string
  default     = "/"
}

variable "ecs_express_min_task_count" {
  description = "Minimum running task count for the ECS Express service."
  type        = number
  default     = 1
}

variable "ecs_express_max_task_count" {
  description = "Maximum running task count for the ECS Express service."
  type        = number
  default     = 2
}

variable "ecs_express_scaling_metric" {
  description = "Autoscaling metric used by ECS Express Mode."
  type        = string
  default     = "AVERAGE_CPU"
}

variable "ecs_express_scaling_target_value" {
  description = "Target value for the ECS Express autoscaling metric."
  type        = number
  default     = 60
}
