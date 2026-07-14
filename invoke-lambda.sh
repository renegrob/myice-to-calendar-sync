#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# Configurable variables
FUNCTION_NAME="aws-ical-sync"
REGION="eu-central-2"
OUT_FILE="out.json"

echo "=== Invoking Lambda: $FUNCTION_NAME ==="

# 1. Run the invoke command and capture the JSON response (which contains LogResult)
RESPONSE=$(aws lambda invoke \
  --function-name "$FUNCTION_NAME" \
  --region "$REGION" \
  --log-type Tail \
  "$OUT_FILE")

echo -e "\n=== Decoded Lambda Logs ==="

# 2. Extract and decode the LogResult using jq and base64
# (Handles differences between macOS/BSD base64 and Linux/GNU base64)
if command -v jq &> /dev/null; then
  ENCODED_LOG=$(echo "$RESPONSE" | jq -r '.LogResult')

  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "$ENCODED_LOG" | base64 -d
  else
    echo "$ENCODED_LOG" | base64 --decode
  fi
else
  echo "Error: 'jq' is not installed. Cannot decode logs cleanly."
  echo "Raw CLI Response was: $RESPONSE"
fi

echo -e "\n=== Returned Lambda Payload ($OUT_FILE) ==="

# 3. Pretty print the returned payload if jq is available
if command -v jq &> /dev/null; then
  jq . "$OUT_FILE"
else
  cat "$OUT_FILE"
fi
echo ""