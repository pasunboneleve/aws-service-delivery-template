Minimal AWS Delivery Platform
=============================

Live URL
--------

<!-- LIVE_URL_START -->
- Service URL: `TODO`
<!-- LIVE_URL_END -->

This repository is a fresh AWS counterpart to the GCP template. It gives
new services a minimal paved road for container delivery with GitHub
Actions, Terraform/OpenTofu, Amazon ECR, and AWS App Runner.

The shape is intentionally small:

- Terraform provisions the AWS-side deployment foundation
- GitHub Actions builds and pushes a container image
- GitHub Actions assumes an AWS role through GitHub OIDC
- App Runner is provisioned by Terraform and updated directly from the workflow

AWS credentials for local Terraform are not stored in `prod.tfvars`.
They come from your shell environment, matching the `AWS_PROFILE` or
`AWS_*` style you use elsewhere.

⚠️ Important: CI/CD requires bootstrap
-----------------------------------

This repository is a template. The workflow is expected to fail until:

- Terraform has provisioned the AWS infrastructure
- the GitHub Actions secrets and variables have been created

Capabilities provided
---------------------

- GitHub Actions to AWS authentication through OIDC, avoiding long-lived CI keys
- ECR repository provisioning for deployable images
- IAM role provisioning for GitHub Actions deployment
- IAM role provisioning for App Runner to pull private images from ECR
- S3-backed Terraform remote state bootstrap script
- Minimal deployment workflow for public HTTP services on App Runner

Architecture overview
---------------------

Typical deployment flow:

```text
Developer push
      |
      v
GitHub Actions workflow
      |
      v
OIDC authentication to AWS
      |
      v
Build container image
      |
      v
Push to Amazon ECR
      |
      v
Update AWS App Runner service
```

Repository structure
--------------------

- `scripts/bootstrap-tf-state.sh`
  Creates and hardens the S3 bucket used for Terraform/OpenTofu state.
- `.github/workflows/deploy.yml`
  Builds the image, pushes it to ECR, and updates App Runner.
- `.env.template`
  Local environment template for AWS and GitHub provider auth.
- `infra/`
  Terraform for OIDC, ECR, IAM roles, App Runner, and GitHub Actions secrets and variables.
- `scripts/update-readme-live-url.sh`
  Updates the live URL block in the README from `tofu output`.

Local verification
------------------

Run:

```bash
./scripts/verify-template-locally.sh
```

This is the cheap local assurance command for the template. It checks Terraform
validity, contract tests under `tests/`, and optional shell and workflow
linters when they are installed locally.

AWS integration skeleton
------------------------

The first Phase 2 AWS integration entrypoint is now present:

```bash
./scripts/run-aws-integration.sh
```

At the moment this is a runner skeleton. It prepares isolated naming and temp
configuration for a real-cloud integration run, and it can now perform the
first isolated foundation apply. Later steps still remain TODO. See
[`docs/aws-integration.md`](docs/aws-integration.md) for the current scope and
TODO boundaries.

Bootstrapping a new project
---------------------------

1. Copy the local environment template and load it:

```bash
cp .env.template .env
direnv allow
```

With `direnv` loaded, `tofu plan`, `tofu apply`, `tofu destroy`, and `tofu import`
automatically use `infra/prod.tfvars`.
If `AWS_PROFILE` is set, `direnv reload` also refreshes exported AWS
session credentials using `aws configure export-credentials`.

2. Create the Terraform state bucket:

```bash
./scripts/bootstrap-tf-state.sh
```

3. Initialize Terraform/OpenTofu:

```bash
cd infra
cp prod.tfvars.template prod.tfvars
tofu init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=$(basename "$(git rev-parse --show-toplevel)")/infra.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="use_lockfile=true"
```

4. Apply the infrastructure:

```bash
tofu apply
```

5. Add your application code and `Dockerfile`.
6. Push to `main` once so GitHub Actions publishes the bootstrap `latest` image to ECR.
7. Run `tofu apply` again so Terraform can create the App Runner service from that image.
8. Refresh the README live URL block:

```bash
./scripts/update-readme-live-url.sh
```

The workflow will build the image, push it to ECR, and update the
Terraform-managed App Runner service.

If the S3 backend refuses to use your AWS CLI profile during `tofu init`,
see the troubleshooting note in [`infra/INFRA.md`](infra/INFRA.md).
If `tofu output -raw service_url` is still empty after the first apply,
that just means the bootstrap image does not exist in ECR yet. Push once,
then rerun `tofu apply`.

Assumptions
-----------

- application code and `Dockerfile` live in the repository root
- deployment targets a public HTTP service on AWS App Runner
- GitHub Actions is the CI/CD system
- Terraform/OpenTofu manages the shared deployment infrastructure
- Terraform manages the GitHub Actions secrets and variables used by CI
- local AWS authentication comes from the shell environment, not tfvars

Scope
-----

This template deliberately avoids ECS, ALB, VPC networking, DNS, and
multi-environment promotion. It is meant to be the smallest AWS delivery
setup that still gives a real commit-to-deploy path.

License: MIT
