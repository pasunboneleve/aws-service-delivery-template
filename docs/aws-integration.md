# AWS Integration Runner

This document describes the Phase 2 AWS integration lane entrypoint:

```bash
./scripts/run-aws-integration.sh
```

Current status:

- the runner is a skeleton
- it prepares isolated naming and temp workspace state
- it writes an isolated integration tfvars file
- it prints the intended command sequence for the real integration flow

Current TODO boundaries:

- wiring the first `tofu apply`
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
