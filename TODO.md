# TODO

## Lower Priority

*(Real, but not urgent)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **10** | CI workflow (GitHub Actions) running `py_compile` + tests on push | 2 | 2 | Nice hygiene, and now unblocked — there are 299 tests to run. Low urgency for a single-maintainer personal project, but would have caught the `rm 'function.zip'` bug (crashes on fresh checkout) before it reached you. |
| **11** | Structured/consistent logging (all `print()` calls emitting JSON, not a mix of f-strings and `json.dumps`) | 2 | 2 | Would make CloudWatch Logs Insights queries easier if this ever needs debugging at 2am. Not costing you anything today at this log volume. |
| **12** | Pre-flight check for the myice credentials SSM parameter in `deploy.sh` | 2 | 2 | `deploy.sh` verifies `/myice-sync/google-service-account` but not `/myice-sync/myice-credentials`, so a deploy succeeds with the credentials missing and fails on the first scheduled invocation instead — on a daily schedule that means waiting for the alert email. The check needs the parameter name out of `sync_configs.py`, and an untested edit to the deploy path was judged the worse risk at the time. Documented in README §4 and `AGENTS.md`. |

## Ideas

*(Designed, deliberately not built)*

### `uid_prefix_remove_overlaps` — replace events left behind by another sync

A per-feed config list naming foreign `uid_prefix` + `source` pairs whose events this sync may delete, on the premise that **myice is the absolute truth for games and trainings**, so an event under one of those prefixes is stale. The `source` allowlist is the safety boundary: an event with no `source` property, or an unlisted one, is never a candidate — that is what keeps hand-made calendar entries safe.

```python
"uid_prefix_remove_overlaps": [
    {"uid_prefix": "aws-ical-game-", "source": "aws-ical-sync"},
],
```

Settled during design:

- **Events created by this sync are out of scope.** `validate_configs` must *reject* an entry whose `source` is `myice-calendar-sync`; our own events stay governed by `plan_sync`'s normal create/update/delete. Retiring one of our own old prefixes is `--purge`'s job, not this feature's.
- **It must reject any `uid_prefix` overlapping a configured feed's own prefix on the same calendar**, reusing the existing bidirectional `startswith` test in `validate_configs`. Without that guard, one feed becomes a deleter of another's events — the Critical bug the final review caught.
- **It belongs in `plan_sync` as its own plan category**, not folded into `plan["delete"]`. The delete loop participates in tombstone/`synced` bookkeeping keyed by *our* UIDs, which foreign events have no business in; a separate category also lets the dry-run report distinguish "left the feed" from "replacing a predecessor". Keeping the decision inside the pure `plan_sync` is what makes the dry run honest by construction.
- **The past guard applies** — a stale foreign event that has already happened is left alone, like everything else here.
- **Ordering is already safe:** `execute_plan` does creates before deletes, so the replacement exists before its predecessor goes.
- **Lookup:** give `list_existing_synced_events` a `source=SOURCE_TAG` parameter rather than a near-duplicate twin, and call it once per configured pair.

**Open question, the reason this is parked:** whether a match requires an identical start/end, or just prefix + source. Exact-match is the conservative "replace", but it is blind to the commonest way these go stale — if myice moves a game from 17:00 to 18:00, the exact-match lookup finds nothing at 18:00 and the stale 17:00 duplicate survives indefinitely. "myice is the absolute truth" argues for prefix + source alone, which also sweeps foreign events myice has dropped entirely. Decide this before writing any code; it changes the data flow, not just a condition.

Also worth settling: if the match is time-insensitive, a myice feed that returns partial data would sweep foreign events wholesale. Check how `sync_club` behaves on an empty/short feed before relying on it.
