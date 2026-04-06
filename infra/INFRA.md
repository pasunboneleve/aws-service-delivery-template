# Infrastructure

This directory provisions the AWS-side delivery foundation:

- an ECR repository for application images
- an IAM OIDC provider for GitHub Actions
- an IAM deploy role that GitHub Actions can assume on `main`
- an App Runner access role that allows the runtime to pull from ECR
- an App Runner service once the bootstrap image exists in ECR
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

Useful outputs include:

- `ecr_repository_url`
- `github_actions_role_arn`
- `app_runner_ecr_access_role_arn`
- `app_runner_service_arn`
- `service_url`

The default App Runner sizing is intentionally small for a template:
`0.25 vCPU` and `0.5 GB` memory per instance. That `0.5 GB` value is
RAM, not container disk.

If `service_url` is `null`, the configured bootstrap tag does not exist in ECR yet. Push one image to `main`, rerun `tofu apply`, then update the README:

```bash
../scripts/update-readme-live-url.sh
```
