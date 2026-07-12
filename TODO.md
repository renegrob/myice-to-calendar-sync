# TODO

## Quick Wins

*(High impact, low effort — do these first)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **1** | Add `num_retries=3` to every `.execute()` call | 4 | 1 | The Google API client library has this built in — `service.events().import_(...).execute(num_retries=3)` — and does exponential backoff on transient 429/5xx errors automatically. Right now a single network blip fails that whole feed's sync for the day with zero retry. This is a find-and-replace across \~4 call sites. |
| **2** | Enforce unique `uid_prefix` at config-load time | 4 | 1 | The docs say prefixes must be unique, but nothing checks it. A copy-paste mistake in `sync_configs.py` (reusing a prefix) would make one feed's deletion pass silently delete another feed's events — exactly the failure mode you've been careful to guard against with purge. A 5-line check in `handler()` before the sync loop turns a silent data-loss bug into a loud startup error. |
| **3** | Remove the `SYNC_CONFIGS`/`SYNC_CONFIGS_PARAM`/`ICAL_URL` fallback code paths | 3 | 1 | These were the source of two real incidents earlier (the shell-escaping JSON breakage, then the stale-deploy confusion where it silently fell back to the broken env var). `sync_configs.py` is now the only path you actually use. Deleting the other three shrinks `handler()`, removes a whole class of "which config source won" confusion, and there's nothing to migrate since nobody's relying on the fallbacks. |
| **4** | Bump Lambda timeout from 30s to \~120s | 3 | 1 | One-line change in `deploy.sh`. Cheap insurance against the "first sync of a new feed = many sequential creates" scenario we discussed — costs nothing extra since Lambda only bills for actual execution time. |
| **5** | Generate `requirements.txt` from `pyproject.toml` instead of hand-maintaining both | 3 | 2 | `pyproject.toml` (uv-managed) and `requirements.txt` (used by `deploy.sh`'s pip fallback) list the same dependencies in two places. If you bump a version in one and forget the other, you get a working uv sync locally but a broken/mismatched Lambda deploy. `uv export --no-dev -o requirements.txt` as a step in `deploy.sh` (or a pre-commit hook) keeps them from drifting. |

## Worth Doing Soon

*(Real gaps, moderate effort)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **6** | Check whether any source feed uses RRULE (recurring events) | 5 *(if applicable)* | 3 | This is the one I'd check first, not last. `cal.walk("VEVENT")` treats each VEVENT block as one event. If a feed expresses "every Tuesday practice" as a single VEVENT + RRULE, your current code syncs it as one occurrence only — silently dropping every recurrence. If your feeds already expand recurrences into individual VEVENTs (common for sports schedules), you're fine and this is a non-issue. Worth a five-minute check against a real feed's raw `.ics` before ruling it in or out. |
| **7** | A minimal test suite | 4 | 3 | Both README and `AGENTS.md` already document a test command that has no tests behind it. Given the bugs so far were exactly the kind unit tests catch (timezone field, change-detection logic, purge scoping), and we already wrote working mocked tests ad hoc in this conversation — porting those into `test_*.py` files is mostly transcription, not new work. |
| **8** | CloudWatch Alarm → notification on Lambda errors | 3 | 2 | Right now, if a sync fails, you find out only if you happen to check logs. A `Lambda Errors >= 1` alarm with an SNS email/SMS target costs a few cents a month at most and closes the loop — "silent failure" is the main remaining reliability gap for a set-and-forget daily job. |
| **9** | A `dry_run` mode for the normal sync, not just purge | 3 | 2 | Mirrors the safety pattern purge already has. Useful whenever you're about to change `sync_configs.py` (new reminder settings, color, format string) and want to see the diff before it actually writes to your calendar. |

## Lower Priority

*(Real, but not urgent)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **10** | CI workflow (GitHub Actions) running `py_compile` + tests on push | 2 | 2 | Nice hygiene once #7 exists; low urgency for a single-maintainer personal project, but would have caught the `rm 'function.zip'` bug (crashes on fresh checkout) before it reached you. |
| **11** | Structured/consistent logging (all `print()` calls emitting JSON, not a mix of f-strings and `json.dumps`) | 2 | 2 | Would make CloudWatch Logs Insights queries easier if this ever needs debugging at 2am. Not costing you anything today at this log volume. |
| **12** | Parallelize independent feed syncs | 1 | 3 | Not a real bottleneck — you've got 3 feeds finishing in seconds total. Skip this unless the feed count grows substantially. |
| **13** | Concurrency guard against overlapping invocations | 1 | 3 | Only matters if you manually invoke while the daily schedule is also mid-run, which is rare and low-consequence (worst case: one wasted API call, not data loss, since `import_()` is idempotent). Not worth the complexity. |
