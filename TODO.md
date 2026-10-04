# TODO

## Lower Priority

*(Real, but not urgent)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **10** | CI workflow (GitHub Actions) running `py_compile` + tests on push | 2 | 2 | Nice hygiene, and now unblocked — there are 259 tests to run. Low urgency for a single-maintainer personal project, but would have caught the `rm 'function.zip'` bug (crashes on fresh checkout) before it reached you. |
| **11** | Structured/consistent logging (all `print()` calls emitting JSON, not a mix of f-strings and `json.dumps`) | 2 | 2 | Would make CloudWatch Logs Insights queries easier if this ever needs debugging at 2am. Not costing you anything today at this log volume. |
| **12** | Pre-flight check for the myice credentials SSM parameter in `deploy.sh` | 2 | 2 | `deploy.sh` verifies `/myice-sync/google-service-account` but not `/myice-sync/myice-credentials`, so a deploy succeeds with the credentials missing and fails on the first scheduled invocation instead — on a daily schedule that means waiting for the alert email. The check needs the parameter name out of `sync_configs.py`, and an untested edit to the deploy path was judged the worse risk at the time. Documented in README §4 and `AGENTS.md`. |
