"""Shared client-side validation helpers for FactTriple and ThreadMessage.

Both dataclasses promise the same thing — a clear Python error naming the
field before any API call — so the checks live once, here.
"""

from datetime import datetime
from typing import Any

SCALARS = (str, int, float, bool, type(None))


def check_timestamp(field: str, value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        # a JSONL row can carry an epoch number; fail with a named error, not
        # an AttributeError
        errors.append(
            f"{field} must be an RFC3339 string (e.g. 2024-06-15T10:30:00Z), "
            f"got {type(value).__name__}: {value!r}"
        )
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} is not RFC3339 (e.g. 2024-06-15T10:30:00Z): {value!r}")


def check_scalar_map(
    field: str, mapping: Any, errors: list[str], *, max_keys: int | None = None
) -> None:
    """Validate an optional dict-of-scalars field (metadata / attributes).

    Rejects non-dict values outright — a CSV column, for instance, arrives as
    a string and must fail with a named error rather than an AttributeError.
    """
    if mapping is None:
        return
    if not isinstance(mapping, dict):
        errors.append(
            f"{field} must be a mapping of scalar values, got {type(mapping).__name__} "
            "(note: CSV columns cannot express mappings — use JSONL or a JSON array)"
        )
        return
    if max_keys is not None and len(mapping) > max_keys:
        errors.append(f"{field} has {len(mapping)} keys; the API allows {max_keys}")
    for key, value in mapping.items():
        if not isinstance(value, SCALARS):
            errors.append(
                f"{field}[{key!r}] must be a scalar (string/number/boolean/null); "
                "nested objects and arrays are not allowed"
            )
