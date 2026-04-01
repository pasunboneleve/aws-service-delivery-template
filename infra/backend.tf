terraform {
  backend "s3" {
    # Configure with -backend-config at init time, for example:
    #   -backend-config="bucket=$TF_STATE_BUCKET"
    #   -backend-config="key=$GITHUB_REPO/infra.tfstate"
    #   -backend-config="region=$AWS_REGION"
    #   -backend-config="use_lockfile=true"
  }
}
