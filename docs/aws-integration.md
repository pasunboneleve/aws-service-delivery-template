# AWS Integration Runner

This document describes the Phase 2 AWS integration lane entrypoint:

```bash
./scripts/run-aws-integration.sh
./scripts/run-aws-integration.sh preflight
./scripts/run-aws-integration.sh run
./scripts/run-aws-integration.sh foundation-apply
./scripts/run-aws-integration.sh bootstrap-publish
./scripts/run-aws-integration.sh second-apply
./scripts/run-aws-integration.sh verify
./scripts/run-aws-integration.sh destroy
```

Use this document for the real-cloud Phase 2 lane only.
For the cheap local Phase 1 contract checks, use:

```bash
./scripts/verify-template-locally.sh
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
- it can run the second isolated apply and fetch the ECS Express service URL
- it can reinitialize the isolated backend and verify the public fixture
  response at the service URL
- it can destroy isolated integration resources automatically on success
- it can destroy a prior isolated run explicitly

Required local tools:

- `tofu`
- `aws`
- `docker`
- `jq`
- `git`
- `python3`

Required environment for real AWS runs:

- `AWS_REGION`
- `TF_STATE_BUCKET`
- `GITHUB_OWNER`
- optional `GITHUB_REPO` if the target GitHub repo name differs from the
  local folder name or cannot be derived from `git origin`
- usable AWS credentials, for example via `AWS_PROFILE`
- GitHub provider auth, for example via `GITHUB_TOKEN`

First real run
--------------

Before touching AWS, run:

```bash
./scripts/run-aws-integration.sh preflight
```

The preflight mode checks:

- required local tools:
  - `tofu`
  - `aws`
  - `docker`
  - `jq`
  - `git`
  - `python3`
- required env vars:
  - `AWS_REGION`
  - `TF_STATE_BUCKET`
  - `GITHUB_OWNER`
- GitHub repo target:
  - `GITHUB_REPO`, or
  - derived from `git remote origin`
- AWS credential source:
  - `AWS_PROFILE`, or
  - `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
- GitHub provider auth source:
  - `GITHUB_TOKEN`, or
  - `github_token` in `infra/prod.tfvars`
- local `infra/prod.tfvars` presence

It does not contact AWS.

When preflight passes, the runner resolves GitHub provider auth from
`GITHUB_TOKEN`, `TF_VAR_github_token`, or `infra/prod.tfvars`, then forwards
that value into isolated Terraform commands via `TF_VAR_github_token`.

Once preflight passes, the normal first real run is:

```bash
./scripts/run-aws-integration.sh run
```

Current naming strategy:

- `integration_prefix`
  derived from `<repo-name>-<run-id>` and slugified
- `github_repo`
  taken from `GITHUB_REPO` when set, otherwise derived from `git remote
  origin`
- `service_name`
  trimmed from the prefix to stay within the current ECS Express service naming budget
- `ecr_repository_name`
  currently matches `service_name`, which mirrors the template's Terraform
  shape
- `ecr_force_delete`
  written as `true` in isolated integration tfvars so teardown can delete the
  fixture image repository after pushing the bootstrap image
- `state_key`
  uses `<repo-name>/integration/<run-id>.tfstate`
- `image_tag`
  uses `integration-<run-id>`

Current TODO boundary:

- scheduled/nightly execution remains a separate follow-up

Account-level behavior:

- normal template stacks still manage their own GitHub Actions IAM OIDC
  provider by default
- the AWS integration runner now checks for an existing provider for
  `https://token.actions.githubusercontent.com` and, when it can read a
  compatible provider, writes isolated tfvars that reuse that ARN instead of
  creating a duplicate
- detecting an existing provider requires AWS IAM read access for:
  - `iam:ListOpenIDConnectProviders`
  - `iam:GetOpenIDConnectProvider`

Current command surface:

- `./scripts/run-aws-integration.sh`
  print the current plan and generated isolated naming
- `./scripts/run-aws-integration.sh preflight`
  check local readiness without contacting AWS
- `./scripts/run-aws-integration.sh run`
  execute the current on-demand AWS integration flow
- `./scripts/run-aws-integration.sh foundation-apply`
  run only the first isolated apply
- `./scripts/run-aws-integration.sh bootstrap-publish`
  push the integration fixture image to ECR
- `./scripts/run-aws-integration.sh second-apply`
  run the second isolated apply and resolve the ECS Express service URL
- `./scripts/run-aws-integration.sh verify`
  verify the public fixture response contract
- `./scripts/run-aws-integration.sh destroy`
  manually tear down a prior isolated run using the same run id/config inputs

Environment variables used by the skeleton:

- `AWS_INTEGRATION_RUN_ID`
  override the generated run id
- `AWS_INTEGRATION_WORKDIR`
  force a specific workdir instead of `mktemp`
- `AWS_INTEGRATION_KEEP_WORKDIR=1`
  keep generated files after the runner exits
- `AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS`
  bound the failure cleanup destroy duration (default: `900`)
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
- successful `run` executions now destroy the isolated integration stack at the
  end of the sequence
- the original failing step and exit code are preserved and reported first
- if cleanup succeeds, that is reported explicitly
- if cleanup is skipped because required isolated inputs were never
  materialized, that is reported explicitly and not treated as a cleanup
  failure
- if cleanup also fails, that is reported as a secondary failure and does not
  mask the original exit
- cleanup destroy is bounded by `AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS`
- when cleanup fails or times out, the runner prints the exact manual destroy
  command for the preserved run id/workdir
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

The `run` mode now performs the full flow and then tears the isolated stack
down on success:

- first apply
- bootstrap image publish
- second apply
- service URL verification
- success-path destroy

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

To run the second apply and fetch the ECS Express service URL, you must provide:

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
If that is still empty, it falls back to `aws ecs describe-express-gateway-service`.
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

To manually destroy a prior run, you must provide:

- `AWS_REGION`
- `TF_STATE_BUCKET`
- `GITHUB_OWNER`
- `AWS_INTEGRATION_RUN_ID`
- `AWS_INTEGRATION_WORKDIR` pointing at the preserved workdir for that run

Then run:

```bash
AWS_REGION=ap-southeast-2 \
TF_STATE_BUCKET=your-state-bucket \
GITHUB_OWNER=your-github-owner \
AWS_INTEGRATION_RUN_ID=20260402173502-24 \
AWS_INTEGRATION_WORKDIR=/tmp/minimal-aws-github-ci-template-20260402173502-24-XXXXXX \
./scripts/run-aws-integration.sh destroy
```

The explicit run id and preserved workdir are required so the runner does not
guess which isolated stack to destroy and can reuse the original GitHub repo
target plus OIDC-management settings from `integration-metadata.json`. For failed runs,
you can obtain the run id and workdir from:

- the runner output
- the preserved integration workdir name
- `cleanup-status.json`
