# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.

## Issue Tracking

This project uses **bd (beads)** for issue tracking.
Run `bd prime` for workflow context, or install hooks (`bd hooks install`) for auto-injection.

**Quick reference:**
- `bd ready` - Find unblocked work
- `bd create "Title" --type task --priority 2` - Create issue
- `bd close <id>` - Complete work
- `bd sync` - Sync with git (run at session end)

For full workflow details: `bd prime`

## Common Development Commands

### Local Verification
Run the standard cheap assurance check:
```bash
./scripts/verify-template-locally.sh
```

This is the Phase 1 local lane. It does not require real AWS calls.

### AWS Integration
Use the Phase 2 runner to inspect or execute the real AWS integration lane:
```bash
./scripts/run-aws-integration.sh
./scripts/run-aws-integration.sh run
```

Required for the real AWS lane:
- `AWS_REGION`
- `TF_STATE_BUCKET`
- `GITHUB_OWNER`
- usable AWS credentials
- local `aws`, `docker`, `tofu`, `jq`, `git`, and `python3`

Current boundary:
- `run` covers apply, bootstrap image publish, URL fetch, verification, and
  success-path destroy
- failure cleanup still runs from the trap path when destructive steps fail
- explicit manual teardown is available through:
  `AWS_INTEGRATION_RUN_ID=<previous-run-id> ./scripts/run-aws-integration.sh destroy`

### AWS Deployment Commands
Set required environment variables first:
```bash
export AWS_PROFILE={{AWS_PROFILE}}
export AWS_REGION={{AWS_REGION}}
export TF_STATE_BUCKET={{TF_STATE_BUCKET}}
```

### Infrastructure Management
Bootstrap Terraform state (one-time):
```bash
AWS_REGION={{AWS_REGION}} TF_STATE_BUCKET={{TF_STATE_BUCKET}} ./scripts/bootstrap-tf-state.sh
```

Apply infrastructure:
```bash
cd infra
tofu init \
  -backend-config="bucket={{TF_STATE_BUCKET}}" \
  -backend-config="key=$(basename \"$(git rev-parse --show-toplevel)\")/infra.tfstate" \
  -backend-config="region={{AWS_REGION}}" \
  -backend-config="use_lockfile=true"
tofu apply
```

## Architecture Overview

### Deployment Architecture
- **AWS App Runner**: Containerized deployment for public HTTP services
- **GitHub Actions CI/CD**: Automated deployment via GitHub OIDC
- **Amazon ECR**: Container image storage
- **Infrastructure as Code**: Terraform/OpenTofu for OIDC, IAM, and ECR

### Infrastructure Components
The `infra/` directory contains Terraform configuration for:
- GitHub OIDC provider in AWS IAM
- GitHub Actions deploy role with ECR and App Runner permissions
- App Runner ECR access role
- GitHub Actions secrets for deployment role ARNs

## Security Considerations
- Container runs as non-root user
- Uses minimal IAM permissions via dedicated roles
- Secrets managed via environment variables, not baked into images

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
