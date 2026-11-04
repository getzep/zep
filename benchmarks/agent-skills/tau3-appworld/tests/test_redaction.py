import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from tau2.data_model.tasks import StructuredUserInstructions, Task, UserScenario
from tau2.runner.helpers import get_tasks

from redaction import Redactor


def _task() -> Task:
    return Task(
        id="task-0",
        description=None,
        user_scenario=UserScenario(
            persona="Mia Li, mia.li3818@example.com",
            instructions=StructuredUserInstructions(
                domain="airline",
                reason_for_call="Use reservation NO6JO3.",
                known_info="Phone 512-555-1212 and user mia_li_3668.",
                unknown_info=None,
                task_instructions="Book flight HAT136 on 1990-04-05.",
            ),
        ),
        initial_state=None,
        evaluation_criteria=None,
        ticket=None,
        issues=None,
        required_documents=None,
        user_tools=None,
    )


def test_task_values_are_redacted_stably() -> None:
    first = Redactor.from_task(_task(), "airline")
    second = Redactor.from_task(_task(), "airline")
    text = "Mia Li mia.li3818@example.com NO6JO3 512-555-1212 mia_li_3668 HAT136"
    assert first.redact(text) == second.redact(text)
    output = first.redact(text)
    assert "NO6JO3" not in output
    assert "mia.li3818@example.com" not in output
    assert "HAT136" not in output


def test_audit_uses_whole_tokens() -> None:
    redactor = Redactor.from_task(_task(), "airline")
    assert redactor.audit("Use <RESERVATION_1>.", redactor.audit_maps()) == []


def test_short_uppercase_address_code_does_not_match_an_ordinary_word() -> None:
    redactor = Redactor()
    redactor._seed_record({"address": {"state": "IN"}})

    assert redactor.redact("Check in before setting the state to IN.") == (
        "Check in before setting the state to <ADDRESS_STATE>."
    )
    assert redactor.audit("Check in before continuing.", redactor.audit_maps()) == []
    assert redactor.audit("Set the state to IN.", redactor.audit_maps()) == ["IN"]


def test_repeated_values_of_one_category_remain_redacted_and_audited() -> None:
    redactor = Redactor()
    first = redactor.redact("Use 2026-01-01 and 2026-02-02.")
    second = redactor.redact("The earlier date was 2026-01-01.")

    assert "2026-01-01" not in first + second
    assert "2026-02-02" not in first + second
    assert redactor.audit("Use 2026-01-01 and 2026-02-02.", redactor.audit_maps()) == [
        "2026-01-01",
        "2026-02-02",
    ]
    assert redactor.replacement_map["<DATE>"] == "2026-01-01"
    assert redactor.replacement_map["<DATE_2>"] == "2026-02-02"


def test_placeholder_remains_unchanged_during_later_pattern_passes() -> None:
    redactor = Redactor()

    assert redactor.redact("Use AB1234 and $12.50.") == ("Use <RESERVATION_1> and <AMOUNT>.")
    assert redactor.redact("<RESERVATION_1234567890>") == "<RESERVATION_1234567890>"


def test_six_letter_instruction_is_not_seeded_as_a_reservation() -> None:
    redactor = Redactor.from_task(
        {"user_scenario": {"instructions": "RETURN item AB1234."}},
        "retail",
    )

    assert redactor.redact("Process the RETURN request for AB1234.") == (
        "Process the RETURN request for <RESERVATION_1>."
    )
    assert "RETURN" not in redactor.replacement_map.values()


def test_opaque_task_identifier_fields_are_redacted_and_audited() -> None:
    redactor = Redactor.from_task(
        {
            "user_scenario": {"instructions": "Find the ticket."},
            "ticket": {"case_id": "opaque-violet"},
        },
        "retail",
    )

    assert redactor.redact("Review case opaque-violet.") == "Review case <TASK_IDENTIFIER>."
    assert redactor.audit("Use opaque-violet.", redactor.audit_maps()) == ["opaque-violet"]


def test_plain_quantities_are_not_audited_as_monetary_values() -> None:
    redactor = Redactor.from_task(
        {"user_scenario": {"instructions": "Refund 2 items for $12.50."}},
        "airline",
    )

    assert redactor.redact("Refund 2 items for $12.50.") == ("Refund 2 items for <AMOUNT>.")
    assert redactor.audit("Check 2 items.", redactor.audit_maps()) == []


def _database(domain: str) -> dict:
    data_root = Path(os.environ["TAU2_DATA_DIR"])
    suffix = "db.toml" if domain == "telecom" else "db.json"
    path = data_root / "tau2" / "domains" / domain / suffix
    if suffix == "db.toml":
        with path.open("rb") as handle:
            return tomllib.load(handle)
    with path.open() as handle:
        return json.load(handle)


def _scenario_text(task: Any) -> str:
    scenario = task.user_scenario.model_dump()
    return json.dumps(scenario, default=str)


def _record_identity_values(record_id: str, record: dict) -> list[str]:
    values = [record_id]
    for key in ("user_id", "customer_id", "email", "phone", "phone_number", "line_id"):
        value = record.get(key)
        if value:
            values.append(str(value))
    for key in ("reservations", "orders", "line_ids", "bill_ids"):
        values.extend(str(value) for value in record.get(key, []))
    return values


@pytest.mark.parametrize("domain", ["airline", "retail", "telecom"])
def test_task_zero_seeds_only_the_scenario_records(domain: str) -> None:
    task = get_tasks(domain, task_split_name="base")[0]
    database = _database(domain)
    scenario_text = _scenario_text(task)
    if domain == "telecom":
        records = {str(record["customer_id"]): record for record in database["customers"]}
    else:
        records = database["users"]
    selected = next(
        (
            (record_id, record)
            for record_id, record in records.items()
            if any(
                len(value) >= 4
                and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", scenario_text, re.IGNORECASE)
                for value in _record_identity_values(record_id, record)
            )
        ),
        None,
    )
    assert selected is not None
    record_id, record = selected
    redactor = Redactor.from_task(task, domain)
    mapped_values = set(redactor.replacement_map.values())
    assert any(value in mapped_values for value in _record_identity_values(record_id, record))

    foreign = next(
        (
            foreign_record
            for foreign_id, foreign_record in records.items()
            if foreign_id != record_id
            and any(
                value and value not in scenario_text and value not in mapped_values
                for value in _record_identity_values(foreign_id, foreign_record)
            )
        ),
        None,
    )
    assert foreign is not None
    foreign_values = _record_identity_values("", foreign)
    assert any(value not in mapped_values for value in foreign_values if value)
