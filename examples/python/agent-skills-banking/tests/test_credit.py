import asyncio
import json
from decimal import Decimal

import pytest

from banking import (
    Credit,
    InterestEntry,
    PackageQuote,
    PackageSelection,
    QuoteRefusal,
    credit_from_quote,
    expected_database,
    make_case,
    quote_package,
)
from runner import episode
from tests.test_runner import scripted_model, valid_operations


@pytest.mark.parametrize("seed", [0, 1, 2, 22, 42, 46, 180, 404, 1009])
def test_credit_uses_the_submitted_package_quote_and_case_statement(seed):
    case = make_case(seed)
    expected = expected_database(case)
    for report, wanted in zip(expected.reports, expected.credits, strict=True):
        request = PackageSelection.model_validate(report.model_dump())
        actual = credit_from_quote(case, request)
        assert isinstance(actual, Credit)
        assert actual == wanted
        assert actual.statement_month == case.statement_month
        assert actual.reason_code == "interest_shortfall"


def test_credit_does_not_select_or_substitute_a_globally_optimal_package():
    case = make_case(1)
    request = PackageSelection(account_id=case.savings[0].account_id)
    quote = quote_package(case, **request.model_dump())
    actual = credit_from_quote(case, request)
    assert isinstance(quote, PackageQuote) and isinstance(actual, Credit)
    assert actual.amount == quote.correction_amount
    assert actual != expected_database(case).credits[0]


@pytest.mark.parametrize("kind", ["unknown-account", "unknown-source", "excluded", "no-shortfall"])
def test_credit_refusal_does_not_create_a_credit(kind, tmp_path):
    case = make_case(1)
    request = PackageSelection(account_id=case.savings[0].account_id)
    if kind == "unknown-account":
        request.account_id = "not-in-this-case"
    elif kind == "unknown-source":
        request.card_id = "not-in-this-case"
    elif kind == "excluded":
        request.checking_account_id = case.checking[1].account_id
        request.card_id = case.cards[1].card_id
    else:
        case.interest_entries.append(
            InterestEntry(
                account_id=request.account_id,
                statement_month=case.statement_month,
                kind="interest",
                amount=Decimal(100000),
            )
        )
    refusal = credit_from_quote(case, request)
    assert isinstance(refusal, QuoteRefusal)
    output = tmp_path / kind
    result = asyncio.run(
        episode(
            output,
            case,
            test_model=scripted_model([("credit_interest", {"account_id": request.account_id})]),
        )
    )
    assert not result["passed"] and result["validation_corrections"] == 0
    assert json.loads((output / "database.json").read_text()) == {"credits": [], "reports": []}
    assert json.loads((output / "tool-calls.json").read_text())[0]["result"]["saved"] is False


def test_valid_but_suboptimal_packages_save_and_fail_the_unchanged_database_score(tmp_path):
    case = make_case(1)
    operations: list[tuple[str, dict]] = [
        (
            "compare_packages",
            {"packages": [{"account_id": account.account_id} for account in case.savings]},
        )
    ]
    for account in case.savings:
        request = {"account_id": account.account_id}
        operations.append(("credit_interest", request))
        operations.append(
            (
                "file_discrepancy_report",
                {
                    **request,
                    "correction_credit_id": f"credit-{account.account_id}-{case.statement_month}",
                },
            )
        )
    output = tmp_path / "suboptimal"
    result = asyncio.run(episode(output, case, test_model=scripted_model(operations)))
    database = json.loads((output / "database.json").read_text())
    assert not result["passed"]
    assert len(database["credits"]) == len(database["reports"]) == 4
    assert result["validation_corrections"] == 0


def test_duplicate_selected_package_credit_does_not_change_the_database(tmp_path):
    case = make_case(1)
    operations = valid_operations(case)
    operations.append(
        next(operation for operation in operations if operation[0] == "credit_interest")
    )
    output = tmp_path / "duplicate"
    result = asyncio.run(episode(output, case, test_model=scripted_model(operations)))
    database = json.loads((output / "database.json").read_text())
    assert result["passed"] and result["validation_corrections"] == 1
    assert database == expected_database(case).model_dump(mode="json")
