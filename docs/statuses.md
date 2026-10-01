# Status handling (`health_status`)

Every myice.hockey record carries a `health_status` field — this is the same field the app's own status selector writes. `classify()` in `lambda_function.py` maps it to an action and a Google Calendar event status:

| `health_status` | Label | Action | Google event status |
|---|---|---|---|
| `1` | Gesund (healthy) | sync | `confirmed` |
| `3` | Temporär (pending your response) | request | `tentative` |
| `6` | Entschuldigt (excused) | remove | — |
| `8` | Krank (sick) | remove | — |
| `9` | Verletzt (injured) | remove | — |
| anything else, or missing | unknown | sync | `confirmed`, with a `WARNING` logged |

`health_status` can arrive as a string or an integer (`classify()` normalizes it with `str()` before comparing), so `3` and `"3"` are treated the same.

## "sync" — business as usual

The event is pushed to the calendar as a normal, confirmed event using `summary_format` (default `"{summary}"`).

## "request" — status `3`, Temporär

myice assigns status `3` itself when a club creates a game or training and you haven't yet accepted or declined it. This is exactly the case myice's own iCal export drops (see the README) — it's the entire reason this project exists rather than just syncing the public feed.

A "request" record gets:
- Its own title template, `request_summary_format` (default `"❓ {summary}"`), instead of `summary_format`.
- Its own color, `request_color_id` if set, else falling back to the entry's `color_id`.
- A Google event `status` of `tentative` rather than `confirmed`, so it visually stands out as "not yet responded to" in Google Calendar's own UI.
- The same treatment propagates to that record's preparation and duty entries — they inherit `tentative` status too (see `prep_body()` / `duty_bodies()`).

## "remove" — statuses `6`, `8`, `9`

If you're excused, sick, or injured, `build_feed()` in `lambda_function.py` skips the record entirely — it contributes **nothing** to the feed: not the main event, not its preparation entry, not any duty entries derived from it. Since the sync's deletion pass works by diffing "what's in the feed" against "what's on the calendar," an event absent from the feed but still present on the calendar gets deleted on the next run (subject to the past-event guard — see below and `docs/dry-run.md`).

This is a deliberate choice, not an oversight: if you can't make a game, there's no reason for any of its calendar artifacts — including a warm-up reminder or a duty assignment that's no longer yours to show up — to keep cluttering your calendar. The event is removed rather than merely marked, so your calendar reflects your actual commitments.

**Caveat:** removal only happens on a future event. The [past-event guard](dry-run.md) means an event that has already started/ended is never deleted by a live sync, even if its status later changes to sick/injured/excused after the fact — that history stays on the calendar as a record of what was scheduled.

## Unknown statuses

If myice ever introduces a new `health_status` value this project doesn't recognize, `classify()` does not guess silently — it logs `WARNING: unknown health_status '<value>'; syncing as confirmed` (visible in CloudWatch for the deployed Lambda, or on stdout locally) and treats the record as a normal confirmed sync. This means a new status won't silently disappear from your calendar, but it also won't automatically get special treatment — check the warning and update `STATUS_ACTIONS` in `lambda_function.py` if a new code needs its own behavior.
