## Deployment Procedures

This file is the shorter execution companion to the root [README](../README.md).
Use the README for the overall flow. Use this document when you are actively
bootstrapping or deploying a project from the template.

### Install dependencies

- [direnv](https://direnv.net/)
- [OpenTofu](https://opentofu.org/) or Terraform
- [AWS CLI](https://docs.aws.amazon.com/cli/)
- [Python 3](https://www.python.org/)

### Set environment variables

```bash
cp .env.template .env
cp infra/prod.tfvars.template infra/prod.tfvars
direnv allow
```

`prod.tfvars` contains repository and service metadata only. AWS credentials should come from your shell environment, for example through `AWS_PROFILE`.
With `direnv` loaded, `tofu plan`, `tofu apply`, `tofu destroy`, and `tofu import`
automatically use `infra/prod.tfvars`.
If `AWS_PROFILE` is set, `direnv reload` also refreshes exported AWS
session credentials.

### Bootstrap the project

1. Bootstrap the S3 backend bucket:

```bash
./scripts/bootstrap-tf-state.sh
```

2. Initialize OpenTofu:

```bash
cd infra
tofu init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=$(basename "$(git rev-parse --show-toplevel)")/infra.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="use_lockfile=true"
```
If backend authentication fails here, see the troubleshooting note in
[`INFRA.md`](INFRA.md).

3. Apply infrastructure:

```bash
tofu apply
```

4. Push an application with a `Dockerfile` to `main`.

Terraform will populate the GitHub Actions secrets and variables used by the workflow.
The GitHub Actions workflow will then build the image, push it to ECR, and update the Terraform-managed ECS Express service.

If the ECR repository is still empty on the first `tofu apply`, Terraform will skip the ECS Express service resource. After the first push populates `latest`, rerun:

```bash
tofu apply
../scripts/update-readme-live-url.sh
```

### Verification options

- Fast local checks before changing the template:

```bash
./scripts/verify-template-locally.sh
```

It runs Terraform checks, the repository contract tests, and optional
`shellcheck` and `actionlint` if they are available locally.

- Real AWS integration readiness and execution:

```bash
../scripts/run-aws-integration.sh preflight
../scripts/run-aws-integration.sh run
```

For the full integration runner behavior, failure handling, and manual destroy
mode, see [../docs/aws-integration.md](../docs/aws-integration.md).
