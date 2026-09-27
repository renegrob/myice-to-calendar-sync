"""
Finds the lines of an event's detail blob that name you.

Game and training details often assign jobs to people:

    Speaker: John Doe
    Zeit: Fam. Brown
    Strafbank: Smith / Green

When a line mentions a configured name, that job becomes its own calendar
entry, so it reads as a distinct commitment instead of being buried in a
description nobody re-reads.

Matching is deliberately loose - case- and accent-insensitive, and a bare
surname counts. A surname shared with another family will therefore produce a
false positive. That is the accepted trade: narrowing the match would silently
miss real duties like "Zeit: Fam. Brown", which is the worse failure.
"""

import re
import unicodedata


def normalise(text: str) -> str:
    """Casefold and strip accents, so César == CESAR == cesar."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold()


def find_duty_lines(details: str, duty_names: list[str]) -> list[str]:
    """
    Return the lines of `details` that mention any of `duty_names`.

    Lines come back in document order, stripped, and deduplicated - a line
    naming two of your names yields one entry, not two.
    """
    if not details or not duty_names:
        return []

    # Word-boundary patterns, built once. \b would not fire correctly next to
    # accented characters after normalisation, but since normalise() reduces
    # everything to ASCII-ish letters, \b is safe here.
    patterns = [
        re.compile(rf"\b{re.escape(normalise(name))}\b")
        for name in duty_names
        if name and name.strip()
    ]
    if not patterns:
        return []

    matched = []
    for raw_line in details.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalised_line = normalise(line)
        if any(p.search(normalised_line) for p in patterns):
            matched.append(line)
    return matched
