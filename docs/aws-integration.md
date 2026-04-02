# AWS Integration Runner

This document describes the Phase 2 AWS integration lane entrypoint:

```bash
./scripts/run-aws-integration.sh
./scripts/run-aws-integration.sh foundation-apply
```

Current status:

- the runner is a skeleton
- it prepares isolated naming and temp workspace state
- it writes isolated integration files:
  - `integration.tfvars`
  - `backend.hcl`
  - `integration-metadata.json`
- it prints the intended command sequence for the real integration flow
- it can run the first isolated `tofu init` + `tofu apply` for foundation resources

Current naming strategy:

- `integration_prefix`
  derived from `<repo-name>-<run-id>` and slugified
- `service_name`
  trimmed from the prefix to stay within the current App Runner naming budget
- `ecr_repository_name`
  currently matches `service_name`, which mirrors the template's Terraform
  shape
- `state_key`
  uses `<repo-name>/integration/<run-id>.tfstate`
- `image_tag`
  uses `integration-<run-id>`

Current TODO boundaries:

- publishing a bootstrap image to ECR
- performing the second `tofu apply`
- fetching and verifying the public App Runner URL
- reliable destroy on partial failures

Environment variables used by the skeleton:

- `AWS_INTEGRATION_RUN_ID`
  override the generated run id
- `AWS_INTEGRATION_WORKDIR`
  force a specific workdir instead of `mktemp`
- `AWS_INTEGRATION_KEEP_WORKDIR=1`
  keep generated files after the runner exits

The runner intentionally does not mutate `infra/prod.tfvars`.
It creates isolated integration config in a temporary working directory.
If you provide `AWS_INTEGRATION_WORKDIR`, that directory is reused and left in
place on exit.

To run the first real foundation apply, you must provide:

- `AWS_REGION`
- `TF_STATE_BUCKET`
- `GITHUB_OWNER`

Then run:

```bash
AWS_REGION=ap-southeast-2 \
TF_STATE_BUCKET=your-state-bucket \
GITHUB_OWNER=your-github-owner \
./scripts/run-aws-integration.sh foundation-apply
```

If you want an interactive `tofu apply`, set:

```bash
AWS_INTEGRATION_AUTO_APPROVE=0
```
