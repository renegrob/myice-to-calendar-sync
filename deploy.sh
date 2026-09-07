#!/usr/bin/env bash
# Deploys the aws-ical-sync Lambda + daily EventBridge Scheduler schedule.
# Prerequisites:
#   - AWS CLI configured (aws configure) with a user/role that can create
#     IAM roles, Lambda functions, EventBridge Scheduler schedules, SNS
#     topics, and CloudWatch alarms.
#   - You have already created a Google service account key and put it
#     into the SSM parameter (see step 3 below) BEFORE running this script.
#   - uv installed (recommended) - used both to package the Lambda and to
#     regenerate requirements.txt from pyproject.toml, keeping the two
#     from drifting apart. Falls back to pip + the existing requirements.txt
#     if uv isn't found (won't auto-refresh requirements.txt in that case).
#   - Optional: copy .env.example to .env and set ALERT_EMAIL to receive
#     failure notifications. .env is gitignored - never commit it.
#
# Usage:
#   ./deploy.sh
#
# Re-running this script after making code changes will update the
# existing function (it's idempotent).

set -euo pipefail

rm -f 'function.zip'

# ---- Config: edit these ----------------------------------------------
FUNCTION_NAME="aws-ical-sync"
REGION="eu-central-2"
SSM_PARAM_NAME="/ical-sync/google-service-account"
SCHEDULE_EXPRESSION="cron(0 5 * * ? *)"   # 05:00 UTC daily - edit as needed
ROLE_NAME="aws-ical-sync-role"
LAMBDA_TIMEOUT=120                        # seconds - headroom for a first
                                           # sync of a new feed (many creates)

# ---- Local/personal config: .env (gitignored, not checked in) --------
# ALERT_EMAIL goes here instead of above, since it's personal data, not
# project config. Copy .env.example to .env and fill it in.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
ALERT_EMAIL="${ALERT_EMAIL:-}"            # from .env; empty skips alerting setup

# ------------------------------------------------------------------------

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "== 1/8 Regenerating requirements.txt from pyproject.toml =="
if command -v uv &> /dev/null; then
  uv export --no-dev --no-hashes -o requirements.txt --quiet
else
  echo "uv not found - skipping regeneration, using existing requirements.txt as-is"
fi

echo "== 2/8 Packaging Lambda =="
PROJECT_DIR="$(pwd)"
BUILD_DIR=$(mktemp -d -t aws-ical-sync-build-XXXXXX)
trap 'rm -rf "$BUILD_DIR"' EXIT

if command -v uv &> /dev/null; then
  echo "Using uv for packaging..."
  uv pip install -r requirements.txt -t "$BUILD_DIR" --quiet --only-binary :all: --python-platform manylinux2014_x86_64 --python-version 3.12
else
  echo "uv not found, falling back to pip..."
  pip install -r requirements.txt -t "$BUILD_DIR" --quiet --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.12 2>/dev/null \
    || pip install -r requirements.txt -t "$BUILD_DIR" --quiet
fi

cp lambda_function.py "$BUILD_DIR"/
if [ -f sync_configs.py ]; then
  cp sync_configs.py "$BUILD_DIR"/
fi
(cd "$BUILD_DIR" && zip -r "${PROJECT_DIR}/function.zip" . -q)
echo "Package size: $(du -h function.zip | cut -f1)"


echo "== 3/8 Creating/updating IAM role =="
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file://trust-policy.json >/dev/null
  echo "Role created, waiting for IAM propagation..."
  sleep 10
fi
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "aws-ical-sync-policy" \
  --policy-document file://lambda-policy.json >/dev/null

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo "== 4/8 Checking SSM parameter exists =="
if ! aws ssm get-parameter --name "$SSM_PARAM_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: SSM parameter $SSM_PARAM_NAME not found in $REGION."
  echo "Create it first with:"
  echo "  aws ssm put-parameter --name \"$SSM_PARAM_NAME\" --type SecureString \\"
  echo "    --value file://service-account-key.json --region $REGION"
  exit 1
fi

echo "== 5/8 Creating/updating Lambda function =="
export SSM_PARAM_NAME
ENV_JSON=$(python3 -c "import json, os; print(json.dumps({'Variables': {'SERVICE_ACCOUNT_PARAM': os.environ['SSM_PARAM_NAME']}}))")

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb://function.zip \
    --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "$ENV_JSON" \
    --timeout "$LAMBDA_TIMEOUT" \
    --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
else
  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler lambda_function.handler \
    --timeout "$LAMBDA_TIMEOUT" \
    --memory-size 256 \
    --zip-file fileb://function.zip \
    --environment "$ENV_JSON" \
    --region "$REGION" >/dev/null
fi

FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" --query 'Configuration.FunctionArn' --output text)

echo "== 6/8 Creating/updating EventBridge Scheduler execution role =="
SCHEDULER_ROLE_NAME="${FUNCTION_NAME}-scheduler-role"
if ! aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$SCHEDULER_ROLE_NAME" \
    --assume-role-policy-document file://scheduler-trust-policy.json >/dev/null
  echo "Role created, waiting for IAM propagation..."
  sleep 10
fi

cat > scheduler-invoke-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "${FUNCTION_ARN}"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "$SCHEDULER_ROLE_NAME" \
  --policy-name "${FUNCTION_NAME}-scheduler-invoke-policy" \
  --policy-document file://scheduler-invoke-policy.json >/dev/null

SCHEDULER_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${SCHEDULER_ROLE_NAME}"

echo "== 7/8 Creating/updating EventBridge Scheduler schedule =="
if aws scheduler get-schedule --name "${FUNCTION_NAME}-daily" --region "$REGION" >/dev/null 2>&1; then
  aws scheduler update-schedule \
    --name "${FUNCTION_NAME}-daily" \
    --schedule-expression "$SCHEDULE_EXPRESSION" \
    --flexible-time-window "Mode=OFF" \
    --target "{\"RoleArn\":\"$SCHEDULER_ROLE_ARN\",\"Arn\":\"$FUNCTION_ARN\"}" \
    --region "$REGION" >/dev/null
else
  aws scheduler create-schedule \
    --name "${FUNCTION_NAME}-daily" \
    --schedule-expression "$SCHEDULE_EXPRESSION" \
    --flexible-time-window "Mode=OFF" \
    --target "{\"RoleArn\":\"$SCHEDULER_ROLE_ARN\",\"Arn\":\"$FUNCTION_ARN\"}" \
    --region "$REGION" >/dev/null
fi

echo "== 8/8 Setting up error alerting =="
if [ -n "$ALERT_EMAIL" ]; then
  TOPIC_ARN=$(aws sns create-topic --name "${FUNCTION_NAME}-alerts" --region "$REGION" --query 'TopicArn' --output text)

  EXISTING_SUB=$(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" --region "$REGION" \
    --query "Subscriptions[?Endpoint=='${ALERT_EMAIL}'] | length(@)" --output text)
  if [ "$EXISTING_SUB" = "0" ]; then
    aws sns subscribe \
      --topic-arn "$TOPIC_ARN" \
      --protocol email \
      --notification-endpoint "$ALERT_EMAIL" \
      --region "$REGION" >/dev/null
    echo "Subscription email sent to $ALERT_EMAIL - you must click the confirmation link before alerts will deliver."
  fi

  aws cloudwatch put-metric-alarm \
    --alarm-name "${FUNCTION_NAME}-errors" \
    --alarm-description "Fires when $FUNCTION_NAME has one or more failed invocations in a 24h window" \
    --namespace "AWS/Lambda" \
    --metric-name "Errors" \
    --dimensions "Name=FunctionName,Value=${FUNCTION_NAME}" \
    --statistic Sum \
    --period 86400 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "$TOPIC_ARN" \
    --region "$REGION" >/dev/null
  echo "Alarm '${FUNCTION_NAME}-errors' -> SNS topic '${FUNCTION_NAME}-alerts' -> $ALERT_EMAIL"
else
  echo "ALERT_EMAIL not set - skipping alarm/notification setup. Set it at the top of this script to enable."
fi

echo "== Done =="
echo "Function: $FUNCTION_ARN"
echo "Schedule: $SCHEDULE_EXPRESSION (via EventBridge Scheduler)"
echo ""
echo "Test it manually with:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --log-type Tail out.json && cat out.json"