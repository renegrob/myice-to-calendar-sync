"""
Sync configuration. Edit this list, then redeploy the Lambda code to apply.

Fields:
  ical_url        (required) - the source ical feed URL
  calendar_id     (required) - target Google Calendar ID (email or calendar ID)
  uid_prefix      (default: "ical-") - namespaces this feed's events so
                   multiple feeds on the same calendar don't collide
  summary_format  (default: "{summary}") - use {summary} as a placeholder,
                   e.g. "Work {summary}"
  color_id        (optional) - Google Calendar event color, "1" through "11":
                   1 Lavender, 2 Sage, 3 Grape, 4 Flamingo, 5 Banana,
                   6 Tangerine, 7 Peacock, 8 Graphite, 9 Blueberry,
                   10 Basil, 11 Tomato
  reminder_minutes (optional) - list of ints, minutes before the event to
                    remind, e.g. [60, 1440] for 1 hour and 1 day before.
                    Omit to just use the calendar's own default reminders.
  reminder_method  (default: "popup") - "popup" or "email"
"""

CONFIGS = [
    {
        # URL of the iCal feed to sync
        "ical_url": "https://example.com/calendar.ics",

        # Google Calendar ID (use "primary" for your main calendar,
        # or the calendar ID for a secondary calendar)
        "calendar_id": "primary",

        # Prefix to namespace UIDs - must be unique per feed to prevent
        # conflicts between different feeds
        "uid_prefix": "example1-",

        # Optional: Format string for event titles. Use {summary} as placeholder.
        # Examples: "{summary}", "📅 {summary}", "{summary} (work)"
        "summary_format": "{summary}",

        # Optional: Google Calendar color ID (1-11). See lambda_function.py COLOR_REFERENCE
        # for the mapping of IDs to color names.
        # "color_id": "11",

        # (optional) - list of ints, minutes before the event to
        #                     remind, e.g. [60, 1440] for 1 hour and 1 day before.
        #                     Omit to just use the calendar's own default reminders.
        # "reminder_minutes": [60],
    },
    # Add more feeds as needed:
    # {
    #     "ical_url": "https://another-example.com/calendar.ics",
    #     "calendar_id": "your.email@gmail.com",
    #     "uid_prefix": "example2-",
    #     "summary_format": "🎉 {summary}",
    #     "color_id": "5",
    #     "reminder_minutes": [60],
    # },
]
