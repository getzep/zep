"""Shared client-side validation helpers for FactTriple and ThreadMessage.

Both dataclasses promise the same thing — a clear Python error naming the
field before any API call — so the checks live once, here.
"""

from datetime import datetime
from typing import Any

from zep_ingest.exceptions import ConfigurationError

SCALARS = (str, int, float, bool, type(None))


def check_len(field: str, value: Any, limit: int, errors: list[str]) -> None:
    """Validate an optional string field's length; non-strings (e.g. a numeric
    JSONL value) fail with a named error instead of a TypeError."""
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(f"{field} must be a string, got {type(value).__name__}: {value!r}")
        return
    if len(value) > limit:
        errors.append(f"{field} exceeds {limit} characters (got {len(value)})")


def check_required_string(field: str, value: Any, limit: int, errors: list[str]) -> None:
    """Validate a required, non-blank string with a maximum length."""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty string")
    check_len(field, value, limit, errors)


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
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} is not RFC3339 (e.g. 2024-06-15T10:30:00Z): {value!r}")
        return
    if parsed.tzinfo is None:
        errors.append(
            f"{field} must include a timezone offset (e.g. 2024-06-15T10:30:00Z): {value!r}"
        )


def check_scalar_map(
    field: str, mapping: Any, errors: list[str], *, max_keys: int | None = None
) -> None:
    """Validate an optional dict-of-scalars field (metadata / attributes).

    Rejects non-dict values outright — a JSON scalar in that position must fail
    with a named error rather than an AttributeError.
    """
    if mapping is None:
        return
    if not isinstance(mapping, dict):
        errors.append(
            f"{field} must be a mapping of scalar values, got {type(mapping).__name__}"
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


def require_int_range(
    field: str,
    value: Any,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    """Validate a public integer configuration value with a consistent error."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field} must be an integer, got {type(value).__name__}")
    if value < minimum or (maximum is not None and value > maximum):
        expected = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ConfigurationError(f"{field} must be {expected}, got {value}")


def require_nonnegative_number(field: str, value: Any) -> None:
    """Validate a public duration/rate configuration value."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigurationError(f"{field} must be a non-negative number, got {value!r}")
