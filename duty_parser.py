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

    # Word-boundary patterns, built once. \b is safe here because Python's re
    # is Unicode-aware for str patterns: the letters that survive normalise()
    # are \w on both sides of the comparison. Do not rely on normalise()
    # yielding ASCII - it does not. NFKD plus combining-mark stripping leaves
    # ø, ł, æ and đ unchanged, and "ß".casefold() becomes "ss". All of those
    # are still word characters, so "Møller" matches "Fam. Møller" but not
    # "Møllerson", which is the intent.
    #
    # The `if name and name.strip()` filter is load-bearing: a blank name would
    # compile to \b\b, which matches at every word boundary and would promote
    # every line in the blob to a duty. Tests pin this.
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
