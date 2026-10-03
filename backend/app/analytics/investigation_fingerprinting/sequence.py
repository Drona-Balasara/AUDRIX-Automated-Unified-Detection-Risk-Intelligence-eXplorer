"""Canonical investigation-sequence representation and edit-distance utilities.

An investigation *fingerprint* is the ordered tuple of ``ActionType`` string
values extracted from an investigation's ``InvestigationAction`` rows, using
``sequence_number`` as the primary sort key and ``occurred_at`` as a
tiebreaker for the rare case of equal sequence numbers.

Separation of concerns
-----------------------
This module is purely computational and has no database or ORM dependency.
It accepts plain Python sequences (lists/tuples of strings) and returns plain
Python values, making it straightforward to test in isolation and reuse from
both the detection engine and future phases.

Normalized edit distance
-------------------------
Sequence comparison uses the **normalized Levenshtein edit distance**
(Wagner–Fischer algorithm), computed without any external dependency:

    distance(a, b) = edit_distance(a, b) / max(len(a), len(b), 1)

The value is in [0.0, 1.0]; 0.0 means identical, 1.0 means completely
dissimilar.  This metric is:

- Symmetric: distance(a, b) == distance(b, a).
- Deterministic: for identical inputs it always returns the same value.
- Interpretable: a distance of 0.3 means roughly 30 % of the longer sequence
  needed to be changed (inserted, deleted, or substituted) to transform one
  into the other.

Sequence-mining precedent (e.g. Levenshtein 1966; process-mining conformance
literature, van der Aalst et al.) and NIST SP 800-61 Rev. 3's emphasis on
structured, repeatable incident-response workflows together motivate using
edit distance to detect investigations that deviate substantially from the
typical workflow pattern or that share suspiciously identical patterns.
"""

from __future__ import annotations

from typing import Sequence


# ---------------------------------------------------------------------------
# Fingerprint type alias
# ---------------------------------------------------------------------------

#: A fingerprint is an immutable, ordered tuple of action-type strings.
Fingerprint = tuple[str, ...]


def build_fingerprint(
    action_records: list[tuple[int, object, str]],
) -> Fingerprint:
    """Build a canonical fingerprint from raw action records.

    Parameters
    ----------
    action_records:
        Each element is a ``(sequence_number, occurred_at, action_type)``
        triple.  ``occurred_at`` may be ``None``; it is used only as a
        tiebreaker for duplicate ``sequence_number`` values.

        - ``sequence_number`` is the primary sort key.
        - ``occurred_at`` is the tiebreaker (``None`` values sort last).
        - ``action_type`` is included verbatim (string, not normalized here).

    Returns
    -------
    Fingerprint
        Sorted tuple of ``action_type`` strings.  Empty tuple when the input
        list is empty — never raises.

    Notes
    -----
    Duplicate sequence numbers are allowed by this function (they can arise
    from data-quality issues or concurrent actions).  The tiebreaker keeps
    output deterministic; the caller can detect duplicates via the
    :func:`has_duplicate_sequence_numbers` helper if needed.
    """
    if not action_records:
        return ()

    def _sort_key(record: tuple[int, object, str]) -> tuple[int, int]:
        seq, ts, _ = record
        # occurred_at may be None or a datetime; convert to a stable int for
        # sorting.  None → very large integer so None timestamps sort last.
        if ts is None:
            ts_key = 10**18
        else:
            try:
                ts_key = int(ts.timestamp() * 1_000_000)  # type: ignore[union-attr]
            except (AttributeError, OSError, OverflowError):
                ts_key = 10**18
        return (seq, ts_key)

    sorted_records = sorted(action_records, key=_sort_key)
    return tuple(r[2] for r in sorted_records)


def has_duplicate_sequence_numbers(
    action_records: list[tuple[int, object, str]],
) -> bool:
    """Return True if any two records share the same sequence_number."""
    seen: set[int] = set()
    for seq, _, _ in action_records:
        if seq in seen:
            return True
        seen.add(seq)
    return False


# ---------------------------------------------------------------------------
# Normalized Levenshtein edit distance
# ---------------------------------------------------------------------------

def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Standard Wagner–Fischer Levenshtein edit distance.

    Counts the minimum number of single-element insertions, deletions, and
    substitutions needed to transform sequence ``a`` into sequence ``b``.
    Runs in O(|a| × |b|) time and O(min(|a|, |b|)) space.
    """
    # Make `a` the shorter sequence for memory efficiency.
    if len(a) > len(b):
        a, b = b, a

    na, nb = len(a), len(b)
    # prev[j] = cost to transform a[:0] into b[:j] (base case: j deletions).
    prev = list(range(nb + 1))

    for i in range(1, na + 1):
        curr = [i] + [0] * nb
        for j in range(1, nb + 1):
            if a[i - 1] == b[j - 1]:
                cost = 0
            else:
                cost = 1
            curr[j] = min(
                curr[j - 1] + 1,       # insertion
                prev[j] + 1,           # deletion
                prev[j - 1] + cost,    # substitution
            )
        prev = curr

    return prev[nb]


def normalized_distance(a: Sequence[str], b: Sequence[str]) -> float:
    """Normalized Levenshtein distance in [0.0, 1.0].

    ``0.0`` means the sequences are identical; ``1.0`` means every element
    would need to change.  Comparing an empty sequence to any sequence
    returns ``1.0`` (unless both are empty, in which case ``0.0``).
    """
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 0.0
    return edit_distance(a, b) / max_len


def similarity(a: Sequence[str], b: Sequence[str]) -> float:
    """Normalized sequence similarity in [0.0, 1.0] (1 − distance)."""
    return 1.0 - normalized_distance(a, b)
