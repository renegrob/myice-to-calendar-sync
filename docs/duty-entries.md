# Duty entries

Games and trainings in myice often carry a free-text detail blob assigning jobs to specific families or players, e.g.:

```
Coach: Anna Keller Tel. 079 111 22 33
Speaker: John Doe
Zeit: Fam. Brown
Strafbank: Smith / Green
```

(This is `BLOB` from `test_duty_entries.py`, used here verbatim as a worked example.)

If you configure `duty_names` on an entry, every line of this blob that mentions one of those names becomes **its own calendar entry**, at the same time and place as the parent event, so a one-off job reads as a distinct commitment instead of being buried three lines down in a description nobody re-reads.

## Where the detail blob comes from

The blob matched against is whatever `event_details()` builds: the record's `notes`, a `Note: ...` line from `health_notes` if present, and a `Meeting time: ...` line if myice supplied a real (non-placeholder) one. In practice the duty lines themselves come from `notes`.

It deliberately does **not** include the `health_status_label` — see [`docs/statuses.md`](statuses.md#why-the-status-is-not-in-the-description). That line was `Status: Gesund` on virtually every record, and while it could never match a real duty name, it was one more line the matcher had to walk past for no benefit.

## Matching rules

Matching is handled by `duty_parser.find_duty_lines()` and is **deliberately loose**:

- **Case-insensitive** — `JOHN DOE`, `john doe`, and `John Doe` all match.
- **Accent-insensitive** — `duty_parser.normalise()` strips combining marks after Unicode NFKD decomposition and casefolds, so `César`, `CESAR`, and `cesar` are equivalent. Note this only strips *combining* accents; letters like `ø`, `ł`, `æ`, `đ` are untouched, and German `ß` casefolds to `ss` — all still match on word boundaries as expected.
- **Word-boundary matching, not full-name matching** — a **bare surname matches**. With `duty_names = ["Brown"]`, the line `Zeit: Fam. Brown` matches, as does a line containing just `Brown` on its own. Word boundaries mean `Brown` matches `Fam. Brown` but not `Brownlow`.
- Each matched line becomes exactly one entry, in document order, deduplicated (a line matching two of your configured names still only produces one entry).
- A blank/whitespace-only name in `duty_names` is explicitly filtered out (it would otherwise match everywhere).

Using the blob above with `duty_names = ["Brown", "John Doe"]` produces two duty entries: one for `Speaker: John Doe` and one for `Zeit: Fam. Brown`. `Strafbank: Smith / Green` and `Coach: Anna Keller Tel. 079 111 22 33` are left alone since no configured name matches them.

## The shared-surname false positive — and what to do about it

Because bare surnames match, **`duty_names = ["Brown"]` matches every line mentioning anyone named Brown**, not just your own family. If another "Fam. Brown" is also listed in the same blob for an unrelated job, you'll get a duty entry that isn't actually yours.

This is an accepted trade-off, not a bug: narrowing the match (for example, requiring a full "First Last" match) would cause real duties written as a bare surname — which is the most common way clubs write them — to be silently missed entirely. A missed duty is the worse failure, so the matching stays loose.

**Mitigation, if it happens to you:**
1. **Delete the stray calendar entry by hand.** This is safe on its own — the next sync run will not recreate it *for that specific instance* as long as the line's text doesn't change, provided the entry's config also sets `"respect_manual_deletions": true` (see [`docs/configuration.md`](configuration.md#when-to-set-respect_manual_deletions)). Without `respect_manual_deletions`, a deleted-but-still-matching duty entry comes back on the next run, same as any other event.

   Note this is the **exception**, not the general pattern. Normally an unwanted event is removed by marking yourself absent in myice, and `respect_manual_deletions` is left off — see [`docs/statuses.md`](statuses.md#how-to-remove-an-event-mark-yourself-absent-in-myice). A false-positive duty is one of the few cases where the calendar genuinely knows something myice cannot express, since the notes really do name someone with your surname and no status change can say "that job isn't mine."
2. **Tighten `duty_names` for that entry** if the false positive recurs often — e.g. add the full name (`"Fam. Brown Senior"` vs. whatever disambiguates in your club's actual notes text) instead of just the surname, if your club's notes are specific enough to support it. There's no per-name "require full match" flag; this is a judgment call made by what you put in the list.

If you see an unexpected duty entry, check the matched line's text before assuming it's a bug — it's very likely doing exactly what loose surname matching is designed to do, just for the wrong family.

## UID stability

A duty entry's `iCalUID` is derived from a SHA-1 hash of the *matched line's normalized text* (see `duty_uid()` in `lambda_function.py`), not its position in the blob. This means:
- Reordering unrelated lines in the detail blob doesn't change a duty entry's UID.
- Editing the matched line's own text **does** change its UID — correctly read by the sync as "that duty disappeared, a new one appeared," since the job description itself changed.
