"""
Sync state, so manual deletions can be made to stick per feed.

For a feed configured with ``respect_manual_deletions``, the sync records every
event it creates/keeps under ``synced``. If you then delete one of those events
*in Google Calendar*, the next run notices that an event it synced before - one
still present in the feed - has disappeared from the calendar, concludes you
cancelled it, and writes a permanent ``tombstone`` so it is never recreated.

Deletion is detected by *absence* (the event is gone from the calendar), so it
does not rely on Google retaining deleted-event tombstones.

Storage is a URI (see ``SYNC_STATE_URI``):
  * a plain path locally (default "sync-state.json", gitignored), or
  * "s3://bucket/key" on Lambda, where the local filesystem is ephemeral - the
    only place the state can survive between invocations. boto3 is already
    available; the Lambda role needs s3:GetObject and s3:PutObject on that key.

The blob is machine-maintained. Past-dated entries are pruned on each save so it
stays small. To un-cancel an event, delete its uid from "tombstones".
"""

import json
import os
from datetime import date
from pathlib import Path

# Where the state lives. Local path by default; set to "s3://bucket/key" on
# Lambda (via the SYNC_STATE_URI env var) so it persists between invocations.
DEFAULT_STATE_URI = os.environ.get("SYNC_STATE_URI", "sync-state.json")


def _empty() -> dict:
    return {"synced": {}, "tombstones": {}}


def _is_s3(uri: str) -> bool:
    return str(uri).startswith("s3://")


def _split_s3(uri: str) -> tuple[str, str]:
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed S3 URI (need s3://bucket/key): {uri!r}")
    return bucket, key


def _normalise(data: dict) -> dict:
    data.setdefault("synced", {})
    data.setdefault("tombstones", {})
    return data


def load(uri: str | Path = DEFAULT_STATE_URI) -> dict:
    uri = str(uri)
    if _is_s3(uri):
        import boto3
        from botocore.exceptions import ClientError

        bucket, key = _split_s3(uri)
        try:
            body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return _empty()  # first run: no state yet
            raise
        return _normalise(json.loads(body))

    p = Path(uri)
    if not p.is_file():
        return _empty()
    return _normalise(json.loads(p.read_text()))


def prune_past(state: dict, today: date) -> dict:
    """Drop entries whose event day is in the past - past events are never
    (re-)created or deleted, so their state no longer matters."""
    for bucket in ("synced", "tombstones"):
        for uid in list(state.get(bucket, {})):
            day = state[bucket][uid].get("date")
            if day and date.fromisoformat(day) < today:
                del state[bucket][uid]
    return state


def save(uri: str | Path = DEFAULT_STATE_URI, state=None, today: date | None = None) -> None:
    state = _empty() if state is None else state
    prune_past(state, today or date.today())
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    uri = str(uri)
    if _is_s3(uri):
        import boto3

        bucket, key = _split_s3(uri)
        boto3.client("s3").put_object(
            Bucket=bucket, Key=key, Body=payload.encode("utf-8"),
            ContentType="application/json",
        )
        return
    Path(uri).write_text(payload)
