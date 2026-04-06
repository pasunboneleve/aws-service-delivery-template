# Changelog

## Unreleased

### Changed

- Migrated the AWS runtime template from AWS App Runner to Amazon ECS Express Mode.
- Replaced App Runner Terraform resources with ECS Express provisioning via CloudFormation, plus ECS task execution and ECS Express infrastructure IAM roles.
- Updated GitHub Actions deployment to build the image, update the ECS Express service, and surface the public service URL.
- Replaced App Runner GitHub Actions variables with ECS Express equivalents:
  - `AWS_ECS_EXPRESS_SERVICE_ARN`
  - `AWS_ECS_CONTAINER_PORT`
- Updated outputs, deployment docs, infrastructure docs, and bootstrap flow documentation to match ECS Express ownership.

### Integration Runner

- Moved the AWS integration runner orchestration from Bash into Python for clearer state handling, subprocess control, and failure cleanup.
- Added local `preflight` readiness checks for tools, AWS inputs, GitHub auth, and isolated Terraform config prerequisites.
- Added isolated integration materialization for:
  - `integration.tfvars`
  - `backend.hcl`
  - `integration-metadata.json`
- Added full integration phases:
  - foundation apply
  - bootstrap image publish
  - second apply
  - public URL verification
  - destroy
- Added colored step labels for clearer success, failure, and cleanup reporting.
- Added trap/finally-style cleanup reporting with:
  - preserved primary failure step and exit code
  - distinct cleanup `succeeded` / `skipped` / `failed` outcomes
  - preserved workdir metadata and destroy logs after failure
  - printed manual destroy command for stuck or timed-out cleanup
- Increased the default cleanup destroy timeout to 15 minutes for slower ECS Express teardown.

### Real-Run Hardening

- Normalized ECS service endpoints to full `https://...` URLs before verification.
- Added force-delete support for integration ECR repositories so pushed fixture images do not block teardown.
- Ensured isolated Terraform runs pass GitHub provider auth via `TF_VAR_github_token`.
- Reinitialized isolated backends before `tofu output` during verification and URL resolution.
- Added plan-safe bootstrap gating for runtime creation based on image presence.
- Added ECS service-linked role bootstrapping via an idempotent helper invoked before ECS Express stack creation.
- Kept Terraform as the owner of long-lived infrastructure while allowing CI to own runtime image rollout.

### Tests

- Expanded helper script tests for:
  - ECS Express service endpoint lookup
  - ECS service-linked role bootstrap
  - ECR image presence
  - GitHub OIDC provider reuse
- Expanded integration runner tests for:
  - isolated backend reinit
  - GitHub token sourcing
  - metadata reuse rules
  - verification fallback behavior
  - cleanup failure reporting
- Updated workflow and ownership contract tests for ECS Express terminology and behavior.
