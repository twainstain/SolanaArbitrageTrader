#!/usr/bin/env bash
# Phase A (admin side) — create the S3 bucket, lock it down, attach the
# runtime IAM policy to the bot's EC2 role. Run from a machine with
# admin-level AWS credentials (typically your laptop).
#
# Idempotent: re-running is safe.
#
# Usage:
#   ./scripts/phase_a_admin.sh                                # defaults
#   ./scripts/phase_a_admin.sh --bucket foo --role foo-role   # overrides
#
# Defaults target the SolanaTrader bot.
set -euo pipefail

BUCKET="yeda-ai-solana-backups"
REGION="us-east-1"
ROLE="solana-trader-ec2-role"
POLICY_NAME="SolanaTraderS3Backup"
PREFIX="daily/"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --policy-name) POLICY_NAME="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "==> Caller identity"
aws sts get-caller-identity

# 1. Bucket — create only if missing
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "==> Bucket $BUCKET already exists"
else
  echo "==> Creating bucket $BUCKET in $REGION"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi

echo "==> Block public access"
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "==> Enable versioning"
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

echo "==> Lifecycle: Glacier IR @ 30d, expire @ 180d (prefix=$PREFIX)"
LC=$(mktemp)
python3 -c "
import json
open('$LC','w').write(json.dumps({
    'Rules': [{
        'ID': 'expire-old-backups',
        'Status': 'Enabled',
        'Filter': {'Prefix': '$PREFIX'},
        'Transitions': [{'Days': 30, 'StorageClass': 'GLACIER_IR'}],
        'Expiration': {'Days': 180},
    }]
}))
"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
  --lifecycle-configuration "file://$LC"
rm -f "$LC"

echo "==> IAM policy (PutObject, GetObject, ListBucket only — no Delete)"
POL=$(mktemp)
python3 -c "
import json
open('$POL','w').write(json.dumps({
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Action': ['s3:PutObject', 's3:GetObject', 's3:ListBucket'],
        'Resource': [
            'arn:aws:s3:::$BUCKET',
            'arn:aws:s3:::$BUCKET/*',
        ],
    }],
}))
"
aws iam put-role-policy --role-name "$ROLE" \
  --policy-name "$POLICY_NAME" \
  --policy-document "file://$POL"
rm -f "$POL"

echo
echo "==> Verification"
echo "--- role policies ---"
aws iam list-role-policies --role-name "$ROLE"
echo "--- bucket versioning ---"
aws s3api get-bucket-versioning --bucket "$BUCKET"
echo "--- bucket lifecycle ---"
aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" 2>/dev/null || echo "(none)"
echo "--- public access block ---"
aws s3api get-public-access-block --bucket "$BUCKET"

echo
echo "DONE. Next: run scripts/phase_a_host.sh on the bot host (EC2)."
