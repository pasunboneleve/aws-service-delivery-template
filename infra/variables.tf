variable "aws_region" {
  description = "AWS region used for ECR, App Runner, and Terraform state."
  type        = string
}

variable "service_name" {
  description = "Base service name used for the ECR repository and App Runner service."
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
  description = "Optional GitHub Personal Access Token with repo scope for managing Actions secrets."
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

variable "apprunner_cpu" {
  description = "App Runner CPU size."
  type        = string
  default     = "1 vCPU"
}

variable "apprunner_memory" {
  description = "App Runner memory size."
  type        = string
  default     = "2 GB"
}
