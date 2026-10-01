# myice.hockey → Google Calendar Sync

Daily job that syncs your myice.hockey games and trainings into Google Calendar — **including entries awaiting your response**, which myice's own iCal export silently drops (it only exports `health_status` `1`, "Gesund"). Runs serverless on AWS Lambda + EventBridge Scheduler.

## How it differs from a plain iCal sync

myice.hockey publishes an iCal feed, but that feed quietly omits any event whose status is `3` ("Temporär" — the club is waiting for you to accept or decline). If you synced from that feed, tentative-but-real invites would simply never show up on your calendar.

Instead, this project logs in to myice.hockey as you and calls `playersfilter`, the same authenticated endpoint the myice web app itself uses to build your schedule. That endpoint returns every record regardless of status, so pending invites, confirmed games, and trainings all make it onto your calendar.

The trade-off: `playersfilter` is an **undocumented private endpoint**, not a published API. It can change shape or move without notice, and using it means storing your real myice.hockey login (username and password) in AWS, not just a feed URL.

---

## 1. Local Development Setup (uv)

This project uses [uv](https://github.com/astral-sh/uv) for fast package management and virtual environment configuration.

1. Install `uv` if you haven't already.
2. Initialize and synchronize the local virtual environment:
   ```bash
   uv sync
   ```
3. Run the test suite:
   ```bash
   uv run python -m unittest discover -s . -p "test_*.py"
   ```

---

## 2. Google Cloud Setup

You need a Google Cloud project, the Calendar API enabled, and a service account whose key gets stored in AWS (never in Google Cloud itself — no Google Cloud costs are involved beyond this one-time setup).

1. Go to https://console.cloud.google.com/ and create a new project.
2. Enable the Calendar API: go to **APIs & Services → Library**, search **Google Calendar API**, and click **Enable**.
3. Create a service account: **APIs & Services → Credentials → Create Credentials → Service account**. Give it any name, e.g. `myice-calendar-sync`. No roles/permissions need to be granted at the project level.
4. Open the service account, go to the **Keys** tab → **Add Key → Create new key → JSON**. This downloads a `.json` key file — keep it, you'll paste its contents into AWS in the next step.
5. Note the service account's email address (e.g. `myice-calendar-sync@your-project.iam.gserviceaccount.com`).

**Share your calendar with the service account:**
1. Open Google Calendar → find the calendar you want events added to (or use your main calendar) → **Settings and sharing**.
2. Under **Share with specific people**, add the service account's email with permission **"Make changes to events."**
3. Note the **Calendar ID** shown further down that settings page (for your primary calendar this is just your own Google account email; for a secondary calendar it looks like `abc123@group.calendar.google.com`).

> [!NOTE]
> Adding attendees/invitees to synced events is **not supported** — Google Calendar API requires Domain-Wide Delegation for service accounts to invite attendees, which is a Google Workspace admin feature unavailable to personal Google accounts.
>
> Per-event reminder overrides are also not functional for the same reason: [reminders are private to whichever identity sets them](https://developers.google.com/workspace/calendar/api/concepts/reminders), and this project authenticates as the service account, not as the calendar's real owner. Set default reminders yourself in Google Calendar's own Settings UI instead — they're per-calendar, so give a feed its own calendar if it needs different reminder behavior.

---

## 3. Store Credentials in AWS

Two SSM parameters are required.

**The Google service account key** (from step 2):

```bash
aws ssm put-parameter \
  --name "/myice-sync/google-service-account" \
  --type SecureString \
  --value file://path/to/your-service-account-key.json \
  --region eu-central-2
```

**Your myice.hockey login**, as a JSON object:

```bash
aws ssm put-parameter \
  --name "/myice-sync/myice-credentials" \
  --type SecureString \
  --value '{"username":"your-myice-username","password":"your-myice-password"}' \
  --region eu-central-2
```

Use the same region here as in `deploy.sh`.

---

## 4. Configure and Deploy

Open `deploy.sh` and edit the config block at the top (`REGION`, `SSM_PARAM_NAME`, `SCHEDULE_EXPRESSION`) if you need something other than the defaults.

Create a `sync_configs.py` file in the project root with your club configurations:

```bash
cp sync_configs_example.py sync_configs.py
# Then edit sync_configs.py with your player ID, club IDs, and calendars
```

> [!IMPORTANT]
> `sync_configs.py` is a **private, untracked file** — it holds your real player/club IDs and calendar IDs and should never be committed (it's already in `.gitignore`). Only `sync_configs_example.py` (placeholder values) belongs in the public repo.

The config model is **one entry per club per event type** — see [docs/configuration.md](docs/configuration.md) for the full field reference, and [docs/capturing-ids.md](docs/capturing-ids.md) for how to find your player ID, club ID, season ID, and login field names in your browser's DevTools.

### Deploy

```bash
./deploy.sh
```

This script will:
1. Package the Lambda using `uv` (with automatic fallback to `pip` if `uv` is missing).
2. Create an IAM role scoped to read the two SSM parameters and write CloudWatch logs.
3. Create/update the Lambda function.
4. Create/update the daily EventBridge Scheduler schedule (with its own dedicated execution role, scoped to just invoking this one function).

---

## 5. Test It

Trigger the sync manually:

```bash
aws lambda invoke \
  --function-name myice-calendar-sync \
  --region eu-central-2 \
  --log-type Tail \
  out.json
cat out.json
```

You should see a list of results per configured club feed, each with `created`, `updated`, `unchanged`, `deleted`, `records_fetched`, and `total_in_feed` counts. Check your Google Calendar to verify the events have appeared.

To tail logs:
```bash
aws logs tail /aws/lambda/myice-calendar-sync --region eu-central-2 --since 1d
```

### Running locally (without deploying)

You can run the exact same sync logic on your machine, reading the Google service-account key from a local file instead of AWS SSM:

```bash
./run-local.sh              # DRY RUN (default) - computes the real plan, writes a report, never writes to Google
./run-local.sh --apply      # LIVE - creates/updates/deletes events on the real calendars
./run-local.sh --help
```

See [docs/dry-run.md](docs/dry-run.md) for the full set of flags, the report format, and why the dry run still needs the service-account key (it reads the calendar to compute a real diff).

---

## 6. Features

- **[Status handling](docs/statuses.md)** — each myice record's `health_status` decides what happens to its calendar entry: a healthy status syncs normally, a pending ("Temporär") status syncs as a tentative "request," and a sick/injured/excused status **removes** the event (and anything derived from it) from the calendar entirely.
- **[Preparation entries](docs/preparation-entries.md)** — optionally add a warm-up/gathering block before each event. The club's own stated meeting time is used when myice supplies one; otherwise it falls back to a configured offset.
- **[Duty entries](docs/duty-entries.md)** — if the event's detail text names you (e.g. "Speaker: Jane Smith"), that line becomes its own calendar entry so a one-off job doesn't get buried in a description. Matching is deliberately loose, which has a documented false-positive trade-off.
- **[Dry run](docs/dry-run.md)** — `./run-local.sh` computes the real sync plan and writes a readable report without ever touching your calendars.

> [!IMPORTANT]
> **Migrating from an older iCal-based sync against the same calendar?** Read [docs/migration.md](docs/migration.md) **before your first `--apply`** — old and new syncs tag events differently, and skipping this step can leave you with duplicate events.

---

## 7. Removing a Feed (Purge)

If you stop needing a club feed (season's over, you left the club, etc.), its already-synced events won't clean themselves up on their own — the daily sync only removes events that *disappear from the myice feed*, not ones you've simply removed from `sync_configs.py`. For that, there's a separate, explicitly-invoked purge mode.

**This never runs automatically.** The daily schedule always invokes the Lambda with an empty payload, so purge only fires when you deliberately pass an `"action": "purge"` payload by hand. It also defaults to a **dry run** — nothing is deleted unless you explicitly pass `"confirm": true`.

**Step 1 — dry run** (always do this first):

```bash
aws lambda invoke \
  --function-name myice-calendar-sync \
  --region eu-central-2 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"purge","calendar_id":"primary","uid_prefix":"myice-a-game-","scope":"all"}' \
  out.json && cat out.json
```

This reports `matched` and `would_delete` counts without touching anything.

**Step 2 — once the counts look right, confirm the delete:**

```bash
aws lambda invoke \
  --function-name myice-calendar-sync \
  --region eu-central-2 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"purge","calendar_id":"primary","uid_prefix":"myice-a-game-","scope":"all","confirm":true}' \
  out.json && cat out.json
```

**Scope options:**

| `scope` | Behavior |
|---|---|
| `"all"` | Deletes every event ever synced under this `uid_prefix` — past and future. Use when fully retiring a feed. |
| `"future"` | Deletes only events that haven't happened yet, leaving already-occurred events as a historical record. Use when stopping a feed mid-season but keeping past history. |

Both `calendar_id` and `uid_prefix` are required in the payload — the Lambda refuses to run without both, rather than guessing which events to delete.

---

## Cost Breakdown

| Resource | Usage | Cost |
|---|---|---|
| Lambda | ~30 invocations/month (daily schedule), 256 MB, up to 120s timeout | Free tier (1M requests + 400,000 GB-seconds/month free) |
| EventBridge Scheduler | 1 daily schedule | Free |
| SSM Parameter Store | 2 SecureString params | Free |
| CloudWatch Logs | small log volume | Free tier |
| S3 (optional) | only if a club sets `respect_manual_deletions`; one small JSON object | Free tier / negligible |

Expected cost: **$0–$0.05/month**, inside the AWS free tier, assuming the resource set above and a single AWS account that isn't already near its free-tier limits elsewhere.
