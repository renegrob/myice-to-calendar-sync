#!/usr/bin/env bash
#
# Run the ical sync locally, reading the Google service-account key from a
# local file instead of AWS SSM. See --help for usage.
set -euo pipefail

cd "$(dirname "$0")"

usage() {
  cat <<'USAGE'
Run the ical sync locally, using a local service-account key instead of AWS SSM.

Usage:
  ./run-local.sh            Read-only PREVIEW - fetch feeds, print what would sync
  ./run-local.sh --apply    Actually create/update/DELETE events on the live calendars
  ./run-local.sh --help     Show this help

Environment:
  GOOGLE_SERVICE_ACCOUNT_FILE  Path to the service-account JSON. Defaults to
                               ./.google-service-account.json, then falls back to
                               ../ehcw-trainings/.google-service-account.json
                               (same Google service account).
  SYNC_STATE_URI               Explicit sync-state location (s3://... or a path).
                               If unset, --apply uses the shared S3 state the
                               Lambda uses (built from STATE_BUCKET in .env).
  LOCAL_STATE=1                Deliberately use the local ./sync-state.json for
                               --apply instead of the shared S3 state.

--preview needs neither a key nor AWS (it never touches Google Calendar or the
sync state). --apply reads the key from the file above; for feeds that use
respect_manual_deletions it also shares the S3 sync state with the Lambda, so a
local run and the Lambda never undo each other's manual-deletion tombstones.
USAGE
}

MODE="--preview"
case "${1:-}" in
  -h | --help) usage; exit 0 ;;
  --apply) MODE="--apply" ;;
  --preview | "") MODE="--preview" ;;
  *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
esac

# Point at a local key file so no AWS access is needed. Prefer this project's
# own key, then reuse the ehcw-trainings one (same Google service account).
if [[ -z "${GOOGLE_SERVICE_ACCOUNT_FILE:-}" ]]; then
  if [[ -f "$PWD/.google-service-account.json" ]]; then
    export GOOGLE_SERVICE_ACCOUNT_FILE="$PWD/.google-service-account.json"
  elif [[ -f "$PWD/../ehcw-trainings/.google-service-account.json" ]]; then
    export GOOGLE_SERVICE_ACCOUNT_FILE="$PWD/../ehcw-trainings/.google-service-account.json"
  fi
fi

if [[ "$MODE" == "--apply" ]]; then
  KEY_FILE="${GOOGLE_SERVICE_ACCOUNT_FILE:-}"
  if [[ -z "$KEY_FILE" || ! -f "$KEY_FILE" ]]; then
    echo "ERROR: service-account key not found." >&2
    echo "Set GOOGLE_SERVICE_ACCOUNT_FILE, or place .google-service-account.json here." >&2
    exit 1
  fi
  echo "Using service-account key: $KEY_FILE"

  # --- Shared sync state (matches the deployed Lambda) ----------------------
  # For feeds with respect_manual_deletions, the state (tombstones + records of
  # what we created) must be shared with the Lambda; a separate local copy would
  # diverge and each would undo the other's deletions. Default to the same S3
  # object the Lambda uses, built from STATE_BUCKET in .env. Set LOCAL_STATE=1
  # to deliberately use ./sync-state.json instead.
  if [ -f .env ]; then set -a; source .env; set +a; fi
  if [[ -z "${SYNC_STATE_URI:-}" && "${LOCAL_STATE:-}" != "1" && -n "${STATE_BUCKET:-}" ]]; then
    AWS_PROFILE="${AWS_PROFILE:-workload}"
    export AWS_PROFILE
    if aws sts get-caller-identity >/dev/null 2>&1; then
      export SYNC_STATE_URI="s3://${STATE_BUCKET}/aws-ical-sync/sync-state.json"
      # The SSO profile's credential provider needs botocore[crt], which the
      # project venv does not ship. Let the AWS CLI resolve credentials and hand
      # them to boto3 as env vars instead (botocore ignores them when
      # AWS_PROFILE is set, so drop it once they are exported).
      if CREDS="$(aws configure export-credentials --format env-no-export 2>/dev/null)"; then
        set -a; eval "$CREDS"; set +a
        unset CREDS AWS_PROFILE
      fi
    else
      echo "ERROR: no valid AWS credentials for profile '$AWS_PROFILE'." >&2
      echo "       --apply shares the S3 sync state with the Lambda. Run" >&2
      echo "       'source ./aws-login.sh' first, or re-run with LOCAL_STATE=1" >&2
      echo "       to use ./sync-state.json (may diverge from the Lambda)." >&2
      exit 1
    fi
  fi
  echo "Sync state: ${SYNC_STATE_URI:-./sync-state.json (local)}"
fi

uv run python run_local.py "$MODE"
