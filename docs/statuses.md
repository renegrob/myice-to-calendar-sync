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

## How to remove an event: mark yourself absent in myice

**myice is the source of truth.** The supported way to get an event off your calendar is to set your status in the myice app — excused, sick or injured — and let the next sync remove it. That removes the event and everything derived from it, and it keeps myice and your calendar agreeing with each other.

Deleting the entry on the calendar instead is **not durable**. By default the sync has no memory of what you deleted by hand, so the record is still in the myice feed, still has a syncable status, and the event is simply recreated on the next run. Nothing warns you; it just reappears.

This is why `respect_manual_deletions` defaults to `False` (see [`docs/configuration.md`](configuration.md)). It exists for cases where the calendar legitimately knows something myice does not — the clearest being a falsely-matched duty entry (see "The shared-surname false positive" in [`docs/duty-entries.md`](duty-entries.md)), where the myice notes really do name someone with your surname and no status change can express "that job isn't mine." For an event you are simply not attending, the status is the right tool, and turning the flag on to paper over a hand-deletion only hides the disagreement between the two systems.

It is a per-entry option, and because `CONFIGS` holds one entry per club per event type, it can be switched on for exactly one club's trainings while every other feed keeps the simpler behaviour.

## Why the status is not in the description

`event_details()` does **not** put the status label into the event description. It used to, and the result was a `Status: Gesund` line on virtually every entry — `1` is the overwhelmingly common status, so the line was the implicit default restated on every event while carrying no information. For `3` the `❓` prefix from `request_summary_format` already says it, and `6`/`8`/`9` never reach a calendar body at all.

The supported way to put the status on the calendar is the **`{status}` placeholder** in any of the summary templates, which makes it opt-in per feed:

```python
"summary_format": "{summary} ({status})",            # always shown
"summary_format": "{summary}{status:? (%)}",         # only when myice set one
"request_summary_format": "❓ {summary} ({status})",  # requests only
```

See [`docs/configuration.md`](configuration.md) for the full placeholder list and the `{field:?prefix%suffix}` conditional syntax. The description now carries only content a human typed: the club's `notes`, a free-text `health_notes`, and a real meeting time.

## Unknown statuses

If myice ever introduces a new `health_status` value this project doesn't recognize, `classify()` does not guess silently — it logs `WARNING: unknown health_status '<value>'; syncing as confirmed` (visible in CloudWatch for the deployed Lambda, or on stdout locally) and treats the record as a normal confirmed sync. This means a new status won't silently disappear from your calendar, but it also won't automatically get special treatment — check the warning and update `STATUS_ACTIONS` in `lambda_function.py` if a new code needs its own behavior.
