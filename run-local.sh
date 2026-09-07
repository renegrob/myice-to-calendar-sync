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

--preview needs no key (it never touches Google Calendar); --apply reads the
key from the file above.
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
fi

uv run python run_local.py "$MODE"
