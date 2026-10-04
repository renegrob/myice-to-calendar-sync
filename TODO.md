# TODO

## Worth Doing Soon

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **1** | Improve Template | 2 | 1 | `@ {place}` renders `@ ` if place is `None`. Use `{place:?@ %}` # Pattern: `{variable:?prefix%suffix}` |

## Lower Priority

*(Real, but not urgent)*

| **#** | **Suggestion** | **Impact** | **Effort** | **Why** |
| :--- | :--- | :---: | :---: | :--- |
| **10** | CI workflow (GitHub Actions) running `py_compile` + tests on push | 2 | 2 | Nice hygiene once #7 exists; low urgency for a single-maintainer personal project, but would have caught the `rm 'function.zip'` bug (crashes on fresh checkout) before it reached you. |
| **11** | Structured/consistent logging (all `print()` calls emitting JSON, not a mix of f-strings and `json.dumps`) | 2 | 2 | Would make CloudWatch Logs Insights queries easier if this ever needs debugging at 2am. Not costing you anything today at this log volume. |
