"""Shared client-side validation helpers for FactTriple and ThreadMessage.

Both dataclasses promise the same thing — a clear Python error naming the
field before any API call — so the checks live once, here.
"""

import math
from datetime import datetime
from typing import Any

from zep_ingest.exceptions import ConfigurationError

SCALARS = (str, int, float, bool, type(None))


def _first_non_finite(value: Any) -> float | None:
    """The first NaN / Infinity / -Infinity in a scalar or an array of scalars.

    They are a Python extension that json accepts on the way in and writes back
    out, so one left in a metadata / attributes value reaches the API as a bare
    ``NaN`` / ``Infinity`` token that no strict JSON parser accepts. Only
    ``float`` can be non-finite — ``bool`` and ``int`` cannot.
    """
    elements = value if isinstance(value, list | tuple) else (value,)
    for element in elements:
        if isinstance(element, float) and not math.isfinite(element):
            return element
    return None


def _is_finite_number(value: int | float) -> bool:
    """Whether a number is usable as a duration or rate. Timing config only.

    ``math.isfinite`` raises OverflowError for an ``int`` too large to convert to
    a float, and such an ``int`` is as unusable downstream as an infinity is —
    ``time.sleep`` and ``time.monotonic() + timeout`` both raise OverflowError on
    it — so catch the overflow and treat the value as non-finite rather than
    letting the check itself raise, or dodging it by accepting the value.

    Deliberately NOT used by the metadata guards above, and the asymmetry is not
    an oversight: JSON integers are arbitrary-precision, so ``10**400`` in a
    metadata value serializes and reparses exactly and is refused by nothing.
    What those guards reject is ``NaN``/``Infinity``, which have no JSON form at
    all. Here the constraint is the opposite one — not "can this be written as
    JSON" but "can the C clock take it" — so it is a float's range that binds.
    """
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def is_scalar_or_scalar_array(value: Any) -> bool:
    """Whether a metadata / attribute value is one the API accepts.

    The API takes a scalar or an array of scalars. An empty array carries no
    meaning and a ``None`` element inside an array is not a value, so both are
    refused; anything nested is refused too. A non-finite float is refused as
    well, at either position — it has no JSON form to send.

    ``str`` is a Python sequence but a scalar here, and a ``set`` has no JSON
    form to send, so only ``list`` and ``tuple`` count as arrays.
    """
    if isinstance(value, SCALARS):
        return _first_non_finite(value) is None
    if isinstance(value, list | tuple):
        return (
            bool(value)
            and all(element is not None and isinstance(element, SCALARS) for element in value)
            and _first_non_finite(value) is None
        )
    return False


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
    """Validate an optional metadata / attributes map against the same rules the
    API applies to both: every key is a non-blank string, and every value is a
    scalar or an array of scalars.

    Rejects non-dict values outright — a JSON scalar in that position must fail
    with a named error rather than an AttributeError.
    """
    if mapping is None:
        return
    if not isinstance(mapping, dict):
        errors.append(
            f"{field} must be a mapping of scalar values or arrays of scalars, "
            f"got {type(mapping).__name__}"
        )
        return
    if max_keys is not None and len(mapping) > max_keys:
        errors.append(f"{field} has {len(mapping)} keys; the API allows {max_keys}")
    for key, value in mapping.items():
        # A JSON object key is a string, so the API takes nothing else — bool
        # included, though Python has already collapsed {True: ..., 1: ...} into
        # one entry by now. Name the offending key rather than coercing it:
        # rewriting 1 to "1" renames a field the caller never wrote.
        if not isinstance(key, str):
            errors.append(f"{field} keys must be strings, got {type(key).__name__}: {key!r}")
        elif not key.strip():
            errors.append(f"{field} keys must be non-empty strings, got {key!r}")
        # A non-finite float is a number, so the generic message below would be
        # misleading about why it was refused — name the value instead.
        non_finite = _first_non_finite(value)
        if non_finite is not None:
            errors.append(
                f"{field}[{key!r}] is {non_finite!r}; NaN, Infinity and -Infinity are not "
                "valid JSON, so the value cannot be sent. Replace it with a finite "
                "number, a string, or null"
            )
        elif not is_scalar_or_scalar_array(value):
            errors.append(
                f"{field}[{key!r}] must only contain scalar values (string, number, "
                "boolean, null) or arrays of scalars; empty arrays, null array "
                "elements, and nested objects or arrays are not allowed"
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
    """Validate a public duration/rate configuration value.

    Finiteness is part of the contract: ``value < 0`` is False for both NaN and
    +inf, so neither would be caught. A NaN interval reaches ``time.sleep`` as a
    ValueError and +inf as an OverflowError, and worse, a non-finite timeout
    silently stops being a timeout — ``elapsed >= nan`` and ``elapsed >= inf``
    are never true, so the poll loop never gives up. (-inf is already refused as
    negative.)

    An ``int`` too large to convert to a float is refused for the same reason: it
    is not representable as one, so it produces those very failures rather than
    escaping them. See ``_is_finite_number``.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not _is_finite_number(value)
        or value < 0
    ):
        raise ConfigurationError(f"{field} must be a finite, non-negative number, got {value!r}")
