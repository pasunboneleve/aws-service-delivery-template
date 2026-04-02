# AWS Integration Runner

This document describes the Phase 2 AWS integration lane entrypoint:

```bash
./scripts/run-aws-integration.sh
./scripts/run-aws-integration.sh foundation-apply
./scripts/run-aws-integration.sh bootstrap-publish
./scripts/run-aws-integration.sh second-apply
./scripts/run-aws-integration.sh verify
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
- it can build and push the bootstrap fixture image to ECR
- it can run the second isolated apply and fetch the service URL
- it can verify the public fixture response at the service URL

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

Current TODO boundary:

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

To publish the bootstrap image, you must provide:

- `AWS_REGION`
- working AWS credentials usable with:
  - `aws sts get-caller-identity`
  - `aws ecr describe-repositories`
  - `aws ecr get-login-password`
- Docker daemon access

Then run:

```bash
AWS_REGION=ap-southeast-2 \
./scripts/run-aws-integration.sh bootstrap-publish
```

The runner uses the repo-local fixture in `integration-fixture/` and pushes the
derived integration tag to the isolated ECR repository name.

To run the second apply and fetch the service URL, you must provide:

- `AWS_REGION`
- `TF_STATE_BUCKET`
- `GITHUB_OWNER`

Then run:

```bash
AWS_REGION=ap-southeast-2 \
TF_STATE_BUCKET=your-state-bucket \
GITHUB_OWNER=your-github-owner \
./scripts/run-aws-integration.sh second-apply
```

The runner first checks `tofu output -raw service_url`.
If that is still empty, it falls back to `aws apprunner list-services`.
If neither path yields a URL, the run fails clearly.

To verify the public fixture response after the second apply, run:

```bash
AWS_REGION=ap-southeast-2 \
TF_STATE_BUCKET=your-state-bucket \
GITHUB_OWNER=your-github-owner \
./scripts/run-aws-integration.sh verify
```

The verification step performs an HTTP GET against the public service URL and
expects this JSON contract:

```json
{
  "status": "ok",
  "service": "minimal-aws-github-ci-template",
  "path": "/"
}
```

You can override the verification path with:

```bash
AWS_INTEGRATION_VERIFY_PATH=/health
```

The runner fails clearly on:

- HTTP errors such as private or broken endpoints
- invalid JSON responses
- unexpected `status`, `service`, or `path` values

The verified response body is saved in the integration workdir as
`verify-response.json`.
