Minimal AWS Delivery Platform
=============================

Live URL
--------

<!-- LIVE_URL_START -->
- Service URL: `TODO`
<!-- LIVE_URL_END -->

This repository is a fresh AWS counterpart to the GCP template. It gives
new services a minimal paved road for container delivery with GitHub
Actions, Terraform/OpenTofu, Amazon ECR, and Amazon ECS Express Mode.

The shape is intentionally small:

- Terraform provisions the AWS-side deployment foundation
- GitHub Actions builds and pushes a container image
- GitHub Actions assumes an AWS role through GitHub OIDC
- ECS Express Mode is provisioned by Terraform and updated directly from the workflow

AWS credentials for local Terraform are not stored in `prod.tfvars`.
They come from your shell environment, matching the `AWS_PROFILE` or
`AWS_*` style you use elsewhere.

⚠️ Important: CI/CD requires bootstrap
--------------------------------------

This repository is a template. The workflow is expected to fail until:

- Terraform has provisioned the AWS infrastructure
- the GitHub Actions secrets and variables have been created

Quick Start
-----------

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

3. Prepare Terraform inputs and initialize the backend:

```bash
cd infra
cp prod.tfvars.template prod.tfvars
tofu init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=$(basename "$(git rev-parse --show-toplevel)")/infra.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="use_lockfile=true"
```

4. Apply infrastructure:

```bash
tofu apply
```

By default the template keeps ECS Express Mode small and cheap: `256`
CPU units (`0.25 vCPU`) and `512` MiB (`0.5 GB`) memory per task. That
memory setting is RAM, not container disk.

5. Add your application code and `Dockerfile`.
6. Push to `main` once so GitHub Actions publishes the bootstrap `latest` image to ECR.
7. Run `tofu apply` again so Terraform can create the ECS Express service from that image.
8. Refresh the README live URL block:

```bash
./scripts/update-readme-live-url.sh
```

The workflow will then build the image, push it to ECR, and update the
Terraform-managed ECS Express service.

Migration note
--------------

Older revisions of this template used AWS App Runner. This template now
targets Amazon ECS Express Mode instead. Existing App Runner consumers
should treat that as a runtime migration rather than an in-place minor
upgrade.

If the S3 backend refuses to use your AWS CLI profile during `tofu init`,
see the troubleshooting note in [`infra/INFRA.md`](infra/INFRA.md).
If `tofu output -raw service_url` is still empty after the first apply,
that just means the bootstrap image does not exist in ECR yet. Push once,
then rerun `tofu apply`.

Local verification
------------------

Run:

```bash
./scripts/verify-template-locally.sh
```

This is the cheap local assurance command for the template. It checks Terraform
validity, contract tests under `tests/`, and optional shell and workflow
linters when they are installed locally.

Real AWS integration
--------------------

The repo also includes a slower real-cloud integration runner:

```bash
./scripts/run-aws-integration.sh
```

- Phase 1: `./scripts/verify-template-locally.sh`
  cheap local contract verification with no real cloud activity
- Phase 2: `./scripts/run-aws-integration.sh`
  slower real AWS integration flow using isolated state, names, and fixture
  images

Phase 2 requires real AWS access. Before running it, make sure you have:

- `tofu`
- `aws`
- `docker`
- `jq`
- `git`
- `python3`
- an AWS profile or exported AWS credentials with permission to:
  - create/update/destroy the template resources
  - push to ECR
  - read ECS Express service state
- a Terraform state bucket in `TF_STATE_BUCKET`
- `GITHUB_OWNER` set for the target GitHub namespace
- the target GitHub repo name available either from `GITHUB_REPO` or from
  `git remote origin`
- GitHub provider auth available via `GITHUB_TOKEN`, `TF_VAR_github_token`,
  or `github_token` in `infra/prod.tfvars`

Recommended environment:

```bash
export AWS_PROFILE=your-profile
export AWS_REGION=ap-southeast-2
export TF_STATE_BUCKET=your-tf-state-bucket
export GITHUB_OWNER=your-github-owner
export GITHUB_TOKEN=your-github-token
direnv reload
```

To inspect the planned integration sequence without touching AWS:

```bash
./scripts/run-aws-integration.sh
```

To verify local readiness before the first real AWS run:

```bash
./scripts/run-aws-integration.sh preflight
```

To run the current end-to-end AWS integration lane on demand:

```bash
./scripts/run-aws-integration.sh run
```

Current behavior of `run`:

- creates an isolated integration workdir and backend key
- forwards GitHub provider auth into isolated Terraform via `TF_VAR_github_token`
- applies foundation infrastructure
- publishes the bootstrap fixture image
- applies again to create/update ECS Express Mode
- fetches the service URL
- verifies the public fixture response
- destroys the isolated stack on success
- force-deletes the ephemeral integration ECR repository on teardown so pushed fixture images do not block cleanup
- if a destructive step fails, attempts cleanup destroy automatically

To manually destroy a prior isolated run:

```bash
AWS_REGION=ap-southeast-2 \
TF_STATE_BUCKET=your-tf-state-bucket \
GITHUB_OWNER=your-github-owner \
AWS_INTEGRATION_RUN_ID=<previous-run-id> \
AWS_INTEGRATION_WORKDIR=/path/to/preserved-workdir \
./scripts/run-aws-integration.sh destroy
```

The explicit `AWS_INTEGRATION_RUN_ID` and preserved
`AWS_INTEGRATION_WORKDIR` are intentional. They prevent the runner from
guessing which isolated stack to tear down and let it reuse the original
GitHub repo target and OIDC-management settings for that run.

Current limitations:

- failed runs preserve their workdir intentionally for cleanup inspection
- cleanup outcomes are recorded in `cleanup-status.json`
- scheduled/nightly execution is not implemented yet

See [`docs/aws-integration.md`](docs/aws-integration.md) for the exact command
surface and operator notes.

More docs
---------

- [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md): project bootstrap and normal deployment steps
- [`infra/INFRA.md`](infra/INFRA.md): infrastructure prerequisites and backend troubleshooting
- [`docs/aws-integration.md`](docs/aws-integration.md): real AWS integration runner details

Assumptions
-----------

- application code and `Dockerfile` live in the repository root
- deployment targets a public HTTP service on Amazon ECS Express Mode
- GitHub Actions is the CI/CD system
- Terraform/OpenTofu manages the shared deployment infrastructure
- Terraform manages the GitHub Actions secrets and variables used by CI
- local AWS authentication comes from the shell environment, not tfvars

Scope
-----

This template deliberately avoids hand-rolled ECS clusters, custom ALB
plumbing, DNS, and multi-environment promotion. It is meant to be the
smallest AWS delivery setup that still gives a real commit-to-deploy
path.

License: MIT
