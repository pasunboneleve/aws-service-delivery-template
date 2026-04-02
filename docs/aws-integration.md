# AWS Integration Runner

This document describes the Phase 2 AWS integration lane entrypoint:

```bash
./scripts/run-aws-integration.sh
./scripts/run-aws-integration.sh run
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
- it can run the full sequence with trap-based cleanup on failure
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

- success-path destroy remains a separate follow-up

Environment variables used by the skeleton:

- `AWS_INTEGRATION_RUN_ID`
  override the generated run id
- `AWS_INTEGRATION_WORKDIR`
  force a specific workdir instead of `mktemp`
- `AWS_INTEGRATION_KEEP_WORKDIR=1`
  keep generated files after the runner exits
- `AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS`
  bound the failure cleanup destroy duration
- `AWS_INTEGRATION_SIMULATE_FAILURE_AT`
  inject local failures into one or more steps for cleanup testing

The runner intentionally does not mutate `infra/prod.tfvars`.
It creates isolated integration config in a temporary working directory.
If you provide `AWS_INTEGRATION_WORKDIR`, that directory is reused and left in
place on exit.

Failure handling behavior:

- the runner emits step logs for:
  - `config-materialization`
  - `first-tofu-apply`
  - `bootstrap-image-publish`
  - `second-tofu-apply`
  - `url-fetch`
  - `verification`
  - `destroy`
- if a destructive mode fails after isolated config is materialized, an `EXIT`
  trap attempts `tofu destroy` using the same generated `backend.hcl`,
  `integration.tfvars`, and run id
- the original failing step and exit code are preserved and reported first
- if cleanup succeeds, that is reported explicitly
- if cleanup is skipped because required isolated inputs were never
  materialized, that is reported explicitly and not treated as a cleanup
  failure
- if cleanup also fails, that is reported as a secondary failure and does not
  mask the original exit
- cleanup destroy is bounded by `AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS`
- generated integration workdirs are preserved after failures so cleanup logs
  and metadata remain available for inspection
- cleanup outcomes are written to `cleanup-status.json` in the integration
  workdir

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

To exercise the full runner with failure cleanup enabled, use:

```bash
AWS_REGION=ap-southeast-2 \
TF_STATE_BUCKET=your-state-bucket \
GITHUB_OWNER=your-github-owner \
./scripts/run-aws-integration.sh run
```

For local failure-path testing without real cloud calls, you can inject a
simulated failure:

```bash
AWS_INTEGRATION_SIMULATE_FAILURE_AT=verification,destroy \
./scripts/run-aws-integration.sh run
```

That is useful for validating that:

- the original failure is reported against the correct step
- cleanup destroy is still attempted
- cleanup skip and cleanup success are clearly distinguished from cleanup
  failure
- generated workdirs and cleanup metadata remain available after failures
- destroy failures are reported as secondary failures

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
