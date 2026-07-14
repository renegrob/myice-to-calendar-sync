# iCal → Google Calendar Sync

Daily job that pulls one or more iCal schedules and mirrors them into Google Calendar. Runs serverless on AWS Lambda + EventBridge Scheduler. Expected cost is $0–$0.05/month (well inside the AWS free tier).

How it stays reliable without a database: every event is pushed to Google via `events.import()` keyed on a prefixed `iCalUID`. Google itself creates the event if the UID is new, or updates it in place if it already exists — so re-running the sync never creates duplicates. Before writing, each event is compared against what's already on the calendar so unchanged events cost zero API calls. Events removed from the source feed are cleaned up by comparing the current feed's UIDs against a tagged set of previously-synced events on the calendar. Events that have already ended are skipped by default (see `SKIP_PAST_EVENTS` below) so the sync never churns through your entire past history on every run.

---

## 1. Local Development Setup (uv)

This project uses [uv](https://github.com/astral-sh/uv) for fast package management and virtual environment configuration.

1. Install `uv` if you haven't already.
2. Initialize and synchronize the local virtual environment (installs development dependencies like `boto3` for local imports while keeping them out of the production Lambda package):
   ```bash
   uv sync
   ```
3. Run local unit tests to verify the sync and fallback parser logic:
   ```bash
   uv run python -m unittest discover -s . -p "test_*.py"
   ```

---

## 2. Google Cloud Setup

You need a Google Cloud project, the Calendar API enabled, and a service account whose key gets stored in AWS (never in Google Cloud itself — no Google Cloud costs are involved beyond this one-time setup).

1. Go to https://console.cloud.google.com/ and create a new project.
2. Enable the Calendar API: go to **APIs & Services → Library**, search **Google Calendar API**, and click **Enable**.
3. Create a service account: **APIs & Services → Credentials → Create Credentials → Service account**. Give it any name, e.g. `aws-ical-sync`. No roles/permissions need to be granted at the project level.
4. Open the service account, go to the **Keys** tab → **Add Key → Create new key → JSON**. This downloads a `.json` key file — keep it, you'll paste its contents into AWS in the next step.
5. Note the service account's email address (e.g. `aws-ical-sync@your-project.iam.gserviceaccount.com`).

**Share your calendar with the service account:**
1. Open Google Calendar → find the calendar you want events added to (or use your main calendar) → **Settings and sharing**.
2. Under **Share with specific people**, add the service account's email with permission **"Make changes to events."**
3. Note the **Calendar ID** shown further down that settings page (for your primary calendar this is just your own Google account email; for a secondary calendar it looks like `abc123@group.calendar.google.com`).

> [!NOTE]
> Adding attendees/invitees to synced events is **not supported** — Google Calendar API requires Domain-Wide Delegation for service accounts to invite attendees, which is a Google Workspace admin feature unavailable to personal Google accounts.
>
> **Per-event custom reminders (`reminder_minutes` in `sync_configs.py`) are also not functional**, for the same underlying reason: [reminders are private to whichever identity sets them](https://developers.google.com/workspace/calendar/api/concepts/reminders), and this project authenticates as the service account, not as the calendar's real owner. Any reminder override the Lambda sets is invisible to you — you'll keep seeing that calendar's own default reminders no matter what.
>
> **Workaround:** default reminders, unlike per-event overrides, are configured by *you* directly in Google Calendar's Settings UI (not via the API) and do apply to what you see. They're per-calendar, not per-event, so if you want different reminder behavior for different feeds, give each feed its own dedicated calendar (repeat the sharing steps above for each) and set that calendar's own default reminders once in **Settings → [calendar name] → Event notifications**. Note reminders are always "N minutes before the event," with no fixed-clock-time option, so something like "the evening before" can only be approximated with a flat offset.

---

## 3. Store the Service Account Key in AWS

Using the `.json` key file downloaded above, store it in AWS Systems Manager Parameter Store:

```bash
aws ssm put-parameter \
  --name "/ical-sync/google-service-account" \
  --type SecureString \
  --value file://path/to/your-service-account-key.json \
  --region eu-central-2
```

Use the same region here as in `deploy.sh`.

---

## 4. Configure and Deploy

Open `deploy.sh` and edit the config block at the top:

- `REGION` — an AWS region close to you.
- `SSM_PARAM_NAME` — path to the SSM parameter created in step 3.
- `SCHEDULE_EXPRESSION` — daily cron schedule (defaults to daily at 05:00 UTC).

Create a `sync_configs.py` file in the project root with your feed configurations:

```bash
cp sync_configs_example.py sync_configs.py
# Then edit sync_configs.py with your feed configurations
```

> [!IMPORTANT]
> `sync_configs.py` is a **private, untracked file** — it holds your real feed URLs and calendar IDs and should never be committed. Make sure it's listed in `.gitignore`. Only `sync_configs_example.py` (with placeholder values) belongs in the public repo.

### Configuration Options

Each entry in `sync_configs.py`'s `CONFIGS` list supports:

| Field | Default | Description |
|---|---|---|
| `ical_url` | *(required)* | URL of the iCal feed to sync |
| `calendar_id` | *(required)* | Google Calendar ID (use `"primary"` for your main calendar) |
| `uid_prefix` | `"ical-"` | Namespaces this feed's events so multiple feeds don't collide. **Must be unique per feed.** |
| `summary_format` | `"{summary}"` | Format string for event titles, e.g. `"🏒 {summary}"`. Use `{summary}` as the placeholder. |
| `color_id` | *(none — calendar default)* | Google Calendar event color, `"1"`–`"11"` (see `COLOR_REFERENCE` in `lambda_function.py`) |
| `reminder_minutes` | *(none)* | ⚠ **Not functional** — see the note in section 2 above. Left here for forward-compatibility only; don't set this. |
| `reminder_method` | `"popup"` | ⚠ **Not functional**, same reason as `reminder_minutes` above. |

> [!IMPORTANT]
> **Use unique `uid_prefix` values** for each configured feed! This isolates their events so that the sync process for one feed doesn't conflict-delete the events synced by another feed.

Other environment variables the Lambda reads:

| Variable | Default | Description |
|---|---|---|
| `SERVICE_ACCOUNT_PARAM` | *(required)* | SSM parameter name holding the Google service account key |
| `DEFAULT_TIMEZONE` | `"Europe/Zurich"` | Fallback timezone for events whose source feed doesn't specify one |
| `SKIP_PAST_EVENTS` | `true` | When true, events that have already ended are neither created nor deleted — just left alone |

### Deploy

```bash
./deploy.sh
```

This script will:
1. Package the Lambda using `uv` (with automatic fallback to `pip` if `uv` is missing).
2. Create an IAM role scoped to read the Google Service Account key SSM parameter and write CloudWatch logs.
3. Create/update the Lambda function.
4. Create/update the daily EventBridge Scheduler schedule (with its own dedicated execution role, scoped to just invoking this one function).

---

## 5. Test It

Trigger the sync manually:

```bash
aws lambda invoke \
  --function-name aws-ical-sync \
  --region eu-central-2 \
  --log-type Tail \
  out.json
cat out.json
```

You should see a list of results per configured feed, each with `created`, `updated`, `unchanged`, `deleted`, `skipped_past`, and `total_in_feed` counts. On a first run everything lands under `created`; on subsequent runs most events should settle into `unchanged` and only the ones that actually moved show up as `updated`. Check your Google Calendar to verify the events have appeared.

To tail logs:
```bash
aws logs tail /aws/lambda/aws-ical-sync --region eu-central-2 --since 1d
```

---

## 6. Removing a Feed (Purge)

If you stop needing a feed (season's over, a schedule moved elsewhere, etc.), its already-synced events won't clean themselves up on their own — the daily sync only removes events that *disappear from the source feed*, not ones you've simply removed from `sync_configs.py`. For that, there's a separate, explicitly-invoked purge mode.

**This never runs automatically.** The daily schedule always invokes the Lambda with an empty payload, so purge only fires when you deliberately pass an `"action": "purge"` payload by hand. It also defaults to a **dry run** — nothing is deleted unless you explicitly pass `"confirm": true`.

**Step 1 — dry run** (always do this first):

```bash
aws lambda invoke \
  --function-name aws-ical-sync \
  --region eu-central-2 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"purge","calendar_id":"primary","uid_prefix":"team-a-","scope":"all"}' \
  out.json && cat out.json
```

This reports `matched` and `would_delete` counts without touching anything.

**Step 2 — once the counts look right, confirm the delete:**

```bash
aws lambda invoke \
  --function-name aws-ical-sync \
  --region eu-central-2 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"purge","calendar_id":"primary","uid_prefix":"team-a-","scope":"all","confirm":true}' \
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
| Lambda | ~30 invocations/month, <15s each | Free tier (1M req/month free) |
| EventBridge Scheduler | 1 daily schedule | Free |
| SSM Parameter Store | 1 SecureString param | Free |
| CloudWatch Logs | small log volume | Free tier |
