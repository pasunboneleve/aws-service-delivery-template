## Deployment Procedures

### Install dependencies

- [direnv](https://direnv.net/)
- [OpenTofu](https://opentofu.org/) or Terraform
- [AWS CLI](https://docs.aws.amazon.com/cli/)

### Set environment variables

```bash
cp .env.template .env
cp infra/prod.tfvars.template infra/prod.tfvars
direnv allow
```

`prod.tfvars` contains repository and service metadata only. AWS credentials should come from your shell environment, for example through `AWS_PROFILE`.

### Initial infrastructure setup

1. Bootstrap the S3 backend bucket:

```bash
./scripts/bootstrap-tf-state.sh
```

2. Initialize OpenTofu:

```bash
cd infra
tofu init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=$GITHUB_REPO/infra.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="use_lockfile=true"
```

3. Apply infrastructure:

```bash
tofu apply -var-file="prod.tfvars"
```

4. Push an application with a `Dockerfile` to `main`.

The GitHub Actions workflow will build the image, push it to ECR, and create or update the App Runner service.
