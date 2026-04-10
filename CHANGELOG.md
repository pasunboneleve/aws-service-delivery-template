# Changelog

## Unreleased

## [v0.3.1] - 2026-04-10

### Changed

- Made GitHub Actions OIDC provider creation explicit in Terraform/OpenTofu with `create_github_oidc_provider`, which now defaults to reusing the current AWS account's standard GitHub OIDC provider ARN instead of trying to create a duplicate provider.
- Updated the isolated AWS integration runner and its tests to match the new OIDC ownership contract and preserve backward compatibility for older integration metadata.
- Updated infrastructure and AWS integration documentation to describe the new default OIDC provider reuse behavior and when to opt into provider creation.
- Switched conditional OIDC provider ARN resolution to `one(...)` for safer Terraform/OpenTofu evaluation.
- Ignored the local `.codex` file in the repository's `.gitignore`.

## [v0.3.0] - 2026-04-06

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
