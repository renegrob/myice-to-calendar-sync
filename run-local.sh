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
  ./run-local.sh                       DRY RUN - plan and write a report file
  ./run-local.sh --games-only          Dry run, games feeds only
  ./run-local.sh --trainings-only      Dry run, training feeds only
  ./run-local.sh --since 2026-01-01    Dry run that may include past events
  ./run-local.sh --out plan.txt        Dry run, explicit report path
  ./run-local.sh --apply               Actually create/update/DELETE events
  ./run-local.sh --help                Show this help

Environment:
  AWS_PROFILE                  Profile to resolve credentials from. EVERY run
                               needs AWS: the myice.hockey login is read from an
                               SSM SecureString and has no local fallback.
  GOOGLE_SERVICE_ACCOUNT_FILE  Path to the service-account JSON. Defaults to
                               ./.google-service-account.json. Without it the
                               key is read from SSM instead.
  SYNC_STATE_URI               Explicit sync-state location (s3://... or a path).
                               If unset, --apply uses the shared S3 state the
                               Lambda uses (built from STATE_BUCKET in .env).
  LOCAL_STATE=1                Deliberately use the local ./sync-state.json for
                               --apply instead of the shared S3 state.

Note: --dry-run reads the calendar to compute a real diff, so it needs Google
credentials (unlike the old --preview, which it replaces). It never writes.
--since is refused with --apply: a live sync never touches the past.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage; exit 0
fi

# Point at this project's own local key file, so reading the calendar needs no
# SSM round-trip. Set GOOGLE_SERVICE_ACCOUNT_FILE to use a key from elsewhere.
if [[ -z "${GOOGLE_SERVICE_ACCOUNT_FILE:-}" && -f "$PWD/.google-service-account.json" ]]; then
  export GOOGLE_SERVICE_ACCOUNT_FILE="$PWD/.google-service-account.json"
fi

# --- AWS credentials for boto3 ----------------------------------------------
# Every run needs AWS, not just --apply: the myice.hockey login lives in an SSM
# SecureString and has no local fallback. This used to sit inside the --apply
# branch, because the old read-only --preview mode touched neither AWS nor
# Google; --dry-run replaced it and does read SSM, so the bootstrap belongs out
# here.
#
# Why bootstrap at all: the project venv does not ship botocore[crt], which the
# SSO login credential provider requires, and chained profiles can trip
# botocore's infinite-loop detector. Letting the AWS CLI resolve credentials and
# handing boto3 static ones as env vars sidesteps both. botocore ignores those
# env vars while AWS_PROFILE is set, so drop it once they are exported.
if [ -f .env ]; then set -a; source .env; set +a; fi

# Naming a profile is explicit intent, so it must win over whatever static
# credentials happen to be in the environment. Without this, exporting
# credentials by hand in your shell (as the troubleshooting docs once suggested)
# silently shadows AWS_PROFILE: an hour later those statics have expired and
# boto3 reports "Credentials were refreshed, but the refreshed credentials are
# still expired" once per feed, while the profile you asked for was never used.
if [[ -n "${AWS_PROFILE:-}" ]]; then
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_CREDENTIAL_EXPIRATION
fi

if [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
  if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "ERROR: no valid AWS credentials${AWS_PROFILE:+ for profile '$AWS_PROFILE'}." >&2
    echo "       Every run reads the myice.hockey login from SSM, so AWS access" >&2
    echo "       is always required. Your SSO session has most likely expired -" >&2
    echo "       log in again (e.g. 'aws sso login --profile ${AWS_PROFILE:-<profile>}'" >&2
    echo "       or 'source ./aws-login.sh'), then re-run." >&2
    exit 1
  fi
  if CREDS="$(aws configure export-credentials --format env-no-export 2>/dev/null)"; then
    set -a; eval "$CREDS"; set +a
    unset CREDS AWS_PROFILE
  fi
fi

if [[ " $* " == *" --apply "* ]]; then
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
  # Credentials were already resolved above, for every run. Here we only pick
  # the state location.
  if [[ -z "${SYNC_STATE_URI:-}" && "${LOCAL_STATE:-}" != "1" && -n "${STATE_BUCKET:-}" ]]; then
    export SYNC_STATE_URI="s3://${STATE_BUCKET}/myice-calendar-sync/sync-state.json"
  fi
  echo "Sync state: ${SYNC_STATE_URI:-./sync-state.json (local)}"
fi

exec uv run python run_local.py "$@"
