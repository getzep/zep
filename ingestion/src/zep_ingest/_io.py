"""Shared row-file reader for the dataclass ingestion paths.

``ingest_fact_triples``, ``ingest_nodes``, and ``ingest_thread_messages`` each
accept a file of rows — JSONL, a JSON array, or one JSON object — with keys matching their
dataclass's fields. JSON is the only file format here because these schemas
carry list and mapping fields (node labels, attributes, metadata) that CSV
cannot express; flat tabular data belongs in ``ingest_json_records``, which
does accept CSV. The dispatch lives once, here, so behavior cannot drift
between the paths.
"""

import json
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

from zep_ingest.exceptions import ConfigurationError


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read rows from JSONL, a JSON array, or one JSON object."""
    if path.suffix.lower() == ".csv":
        raise ConfigurationError(
            f"CSV is not supported for this ingestion path ({path.name}); use JSONL, "
            "a JSON object, or a JSON array. They can express the list and mapping fields (node "
            "labels, attributes, metadata) that CSV cannot. For flat tabular records, "
            "use ingest_json_records, which does accept CSV."
        )
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        # an empty file parses as zero JSONL rows, so without this the run
        # succeeds having ingested nothing — usually a wrong path, not intent
        raise ConfigurationError(
            f"{path.name} is empty. Pass a file with rows, or an explicit "
            "[] if ingesting nothing is deliberate."
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"Could not parse {path.name} as JSON/JSONL: {error}"
            ) from error
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    raise ConfigurationError(f"{path.name} must contain JSON objects, not {type(parsed).__name__}.")


def rows_to_fields(rows: list[dict[str, Any]], row_type: type) -> list[dict[str, Any]]:
    """Validate row shapes against a dataclass and retain supplied fields exactly.

    Unknown columns are rejected because silently dropping a misspelled public
    field can produce a valid-looking but semantically incomplete ingestion.
    Omitted required columns are rejected here too, so a chat export missing
    ``name`` or ``created_at`` names the field and the row rather than surfacing
    as a bare TypeError from the dataclass constructor. Both the allowed and the
    required sets are read off the dataclass, so neither can drift from it.
    """
    spec = fields(row_type)
    allowed = frozenset(field.name for field in spec)
    required = [
        field.name
        for field in spec
        if field.default is MISSING and field.default_factory is MISSING
    ]
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConfigurationError(f"Row {index} must be a JSON object, got {type(row).__name__}")
        unknown = sorted(set(row) - allowed)
        if unknown:
            raise ConfigurationError(
                f"Row {index} has unknown field(s): {', '.join(unknown)}. "
                f"Expected fields: {', '.join(sorted(allowed))}."
            )
        missing = [name for name in required if name not in row]
        if missing:
            raise ConfigurationError(
                f"Row {index} is missing required field(s): {', '.join(missing)}. "
                f"Required fields: {', '.join(required)}."
            )
        validated.append(dict(row))
    return validated
