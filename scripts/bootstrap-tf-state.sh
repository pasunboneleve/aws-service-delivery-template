#!/usr/bin/env bash
set -euo pipefail

# Bootstrap an S3 bucket for Terraform/OpenTofu state with basic hardening.
# Usage:
#   AWS_REGION=<region> TF_STATE_BUCKET=<name> ./scripts/bootstrap-tf-state.sh
# Optional: RETENTION_DAYS (defaults to 30)

: "${AWS_REGION:?Set AWS_REGION}"
: "${TF_STATE_BUCKET:?Set TF_STATE_BUCKET}"
RETENTION_DAYS=${RETENTION_DAYS:-30}

echo "Creating bucket s3://${TF_STATE_BUCKET} in ${AWS_REGION} if needed"
if ! aws s3api head-bucket --bucket "${TF_STATE_BUCKET}" >/dev/null 2>&1; then
  if [ "${AWS_REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${TF_STATE_BUCKET}"
  else
    aws s3api create-bucket \
      --bucket "${TF_STATE_BUCKET}" \
      --region "${AWS_REGION}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}"
  fi
fi

echo "Enabling bucket versioning"
aws s3api put-bucket-versioning \
  --bucket "${TF_STATE_BUCKET}" \
  --versioning-configuration Status=Enabled

echo "Enforcing server-side encryption"
aws s3api put-bucket-encryption \
  --bucket "${TF_STATE_BUCKET}" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

echo "Blocking public access"
aws s3api put-public-access-block \
  --bucket "${TF_STATE_BUCKET}" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "Applying lifecycle retention for noncurrent versions"
aws s3api put-bucket-lifecycle-configuration \
  --bucket "${TF_STATE_BUCKET}" \
  --lifecycle-configuration "{\"Rules\":[{\"ID\":\"cleanup-old-state-versions\",\"Status\":\"Enabled\",\"Filter\":{\"Prefix\":\"\"},\"NoncurrentVersionExpiration\":{\"NoncurrentDays\":${RETENTION_DAYS}}}]}"

echo "Done. Use this backend config:"
echo "  -backend-config=\"bucket=${TF_STATE_BUCKET}\""
echo "  -backend-config=\"key=<repo>/infra.tfstate\""
echo "  -backend-config=\"region=${AWS_REGION}\""
echo "  -backend-config=\"use_lockfile=true\""
