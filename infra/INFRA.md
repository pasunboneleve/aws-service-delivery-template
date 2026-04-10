# Infrastructure

This directory provisions the AWS-side delivery foundation:

- an ECR repository for application images
- reuse of the account's GitHub Actions IAM OIDC provider by default, or
  creation of one when explicitly requested
- an IAM deploy role that GitHub Actions can assume on `main`
- an ECS task execution role that allows the runtime to pull from ECR
- an ECS Express infrastructure role for AWS-managed networking and scaling
- an ECS Express service once the bootstrap image exists in ECR
- GitHub Actions secrets for role ARNs
- GitHub Actions variables for region, repository, and service settings

## Prerequisites

- AWS credentials available in your shell via `AWS_PROFILE` or the standard `AWS_*` environment variables
- `GITHUB_TOKEN` available in your shell if you want Terraform to manage the repository secret
- OpenTofu or Terraform 1.6+
- AWS CLI for backend bootstrap

## 1) Create a remote state bucket

```bash
export AWS_REGION=ap-southeast-2
export TF_STATE_BUCKET=your-unique-tf-state-bucket
./scripts/bootstrap-tf-state.sh
```

## 2) Initialize the S3 backend

```bash
cd infra
tofu init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=$(basename "$(git rev-parse --show-toplevel)")/infra.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="use_lockfile=true"
```

If `tofu init` fails with `No valid credential sources found` while
using an AWS CLI profile, export temporary credentials into the
environment first:

```bash
eval "$(
  aws configure export-credentials \
    --profile <your-profile> \
    --format env
)"
```

Then rerun `tofu init`. This sometimes affects the S3 backend even when
the AWS CLI can successfully use the same profile.

## 3) Apply the infrastructure

```bash
cp prod.tfvars.template prod.tfvars
tofu apply
```

By default, the stack reuses the current AWS account's standard GitHub
Actions OIDC provider ARN. Set `create_github_oidc_provider = true` only
if you want this stack to create and manage
`https://token.actions.githubusercontent.com` itself.

Useful outputs include:

- `ecr_repository_url`
- `github_actions_role_arn`
- `ecs_task_execution_role_arn`
- `ecs_express_infrastructure_role_arn`
- `ecs_express_service_arn`
- `service_url`

The default ECS Express sizing is intentionally small for a template:
`256` CPU units (`0.25 vCPU`) and `512` MiB (`0.5 GB`) memory per task.
That memory value is RAM, not container disk.

If `service_url` is `null`, the configured bootstrap tag does not exist in ECR yet. Push one image to `main`, rerun `tofu apply`, then update the README:

```bash
../scripts/update-readme-live-url.sh
```
