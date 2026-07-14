# TODO

## Worth Doing Soon

*(Real gaps, moderate effort)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **6** | Check whether any source feed uses RRULE (recurring events) | 5 *(if applicable)* | 3 | This is the one I'd check first, not last. `cal.walk("VEVENT")` treats each VEVENT block as one event. If a feed expresses "every Tuesday practice" as a single VEVENT + RRULE, your current code syncs it as one occurrence only — silently dropping every recurrence. If your feeds already expand recurrences into individual VEVENTs (common for sports schedules), you're fine and this is a non-issue. Worth a five-minute check against a real feed's raw `.ics` before ruling it in or out. |
| **7** | A minimal test suite | 4 | 3 | Both README and `AGENTS.md` already document a test command that has no tests behind it. Given the bugs so far were exactly the kind unit tests catch (timezone field, change-detection logic, purge scoping), and we already wrote working mocked tests ad hoc in this conversation — porting those into `test_*.py` files is mostly transcription, not new work. |
| **9** | A `dry_run` mode for the normal sync, not just purge | 3 | 2 | Mirrors the safety pattern purge already has. Useful whenever you're about to change `sync_configs.py` (new reminder settings, color, format string) and want to see the diff before it actually writes to your calendar. |

## Lower Priority

*(Real, but not urgent)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **10** | CI workflow (GitHub Actions) running `py_compile` + tests on push | 2 | 2 | Nice hygiene once #7 exists; low urgency for a single-maintainer personal project, but would have caught the `rm 'function.zip'` bug (crashes on fresh checkout) before it reached you. |
| **11** | Structured/consistent logging (all `print()` calls emitting JSON, not a mix of f-strings and `json.dumps`) | 2 | 2 | Would make CloudWatch Logs Insights queries easier if this ever needs debugging at 2am. Not costing you anything today at this log volume. |
| **12** | Parallelize independent feed syncs | 1 | 3 | Not a real bottleneck — you've got 3 feeds finishing in seconds total. Skip this unless the feed count grows substantially. |
| **13** | Concurrency guard against overlapping invocations | 1 | 3 | Only matters if you manually invoke while the daily schedule is also mid-run, which is rare and low-consequence (worst case: one wasted API call, not data loss, since `import_()` is idempotent). Not worth the complexity. |
