# Preparation entries

A preparation entry is a separate calendar event — a warm-up or gathering block — placed right before the main event, ending exactly when it starts. It's opt-in per `sync_configs.py` entry via `prep_minutes`.

## Turning it on and off

- `prep_minutes` **omitted**, or set to `0` — no preparation entry is created. Zero is treated as a deliberate "off," not an error, and produces no warning.
- `prep_minutes` a **positive integer** — a preparation entry is created, defaulting to that many minutes before the event start.
- `prep_minutes` **negative** — treated as off (no entry created), but `prep_body()` logs `WARNING: prep_minutes is <N>; it must be positive. No preparation entry will be created.` A negative offset would make the entry end before it starts, so this is caught rather than producing an inverted event. `validate_configs()` also rejects a negative `prep_minutes` outright at config-load time with a `RuntimeError`, so in practice this warning path is a defense-in-depth check inside `prep_body()` itself, not something a normal run hits.

## The record's own meeting time wins

Many myice records include a `meeting` field — the club's own stated gathering time for that specific event. When present and sane, `prep_start()` (in `lambda_function.py`) uses it **instead of** the configured `prep_minutes` offset:

- If `meeting` is empty, `"00:00"`, or `"00:00:00"` (myice's placeholder for "not set"), it's treated as absent and the `prep_minutes` offset is used.
- Otherwise, the meeting time is parsed as `HH:MM[:SS]` on the event's own date, in the entry's timezone.
- **If that parsed meeting time is at or after the event's start time, it's rejected as bad data** — `prep_start()` logs `WARNING: meeting time '<value>' is at or after the event start; using prep_minutes` and falls back to the offset. A meeting time can't sensibly be at or after the game itself.
- If the meeting time is unparseable (doesn't split into at least `HH:MM`), the same fallback happens with `WARNING: unparseable meeting time '<value>'; using prep_minutes`.

So in practice: a real, sane meeting time from myice always wins; anything missing, placeholder, or nonsensical falls back to your configured offset.

## Example: different warm-up lengths per club and event type

Because `prep_minutes` is set per `sync_configs.py` entry (one entry per club per event type — see `docs/configuration.md`), a club's games and trainings can have entirely different warm-up lengths, and different clubs can differ too:

```python
CONFIGS = [
    {   # Club A - games: arrive 90 minutes early
        **_SHARED,
        "myice_event_type": "g",
        "myice_club": "101",
        "uid_prefix": "myice-a-game-",
        "prep_minutes": 90,
    },
    {   # Club A - trainings: only 20 minutes early
        **_SHARED,
        "myice_event_type": "p",
        "myice_club": "101",
        "uid_prefix": "myice-a-training-",
        "prep_minutes": 20,
    },
    {   # Club B - games: no preparation entries at all
        **_SHARED,
        "myice_event_type": "g",
        "myice_club": "202",
        "uid_prefix": "myice-b-game-",
        # prep_minutes omitted
    },
]
```

## Other preparation entry details

- **Title** — `prep_summary_format`, default `"Warm-up: {summary}"`, where `{summary}` is the record's age group and opponent/name (same value the main event's title uses).
- **Color** — `prep_color_id` if set, else the entry's own `color_id`, else the calendar default.
- **Location and description** — copied from the record itself (the same source `record_to_google_body()` uses), not from the already-built parent event body. This matters if you ever call `prep_body()` with a stale or mismatched `parent_body` — the preparation entry's content is still correct because it's derived independently from the record.
- **Status** — inherits whatever Google status (`confirmed`/`tentative`) the parent event got from [status classification](statuses.md); a pending ("Temporär") game's preparation entry is tentative too.
- **UID** — `<uid_prefix>prep-<id_game>`, deliberately **not** derived from the meeting time, so that a club editing the meeting time updates this entry in place rather than deleting and recreating it.
