import asyncio
import json
from decimal import Decimal

import pytest

from banking import (
    AllocationQuote,
    PackageSelection,
    QuoteRefusal,
    Report,
    ReportRequest,
    compare_packages,
    expected_database,
    make_case,
    quote_package,
    report_from_quote,
)


def permitted_packages(case):
    return [
        PackageSelection(account_id=account.account_id, checking_account_id=checking, card_id=card)
        for account in case.savings
        for checking in [None] + [item.account_id for item in case.checking]
        for card in [None] + [item.card_id for item in case.cards]
        if not isinstance(quote_package(case, account.account_id, checking, card), QuoteRefusal)
    ]


def test_runtime_manifest_excludes_tests_and_diagnostic_scripts():
    from runner import source_hashes

    assert set(source_hashes()) == {
        "audit.py",
        "banking.py",
        "hosted.py",
        "pyproject.toml",
        "runner.py",
        "uv.lock",
        "walkthrough.py",
    }


@pytest.mark.parametrize("seed", [0, 1, 2, 22, 54, 101, 404, 532, 999, 1000])
def test_complete_candidates_match_the_independent_scorer(seed):
    case = make_case(seed)
    allocation = compare_packages(case, permitted_packages(case))
    assert isinstance(allocation, AllocationQuote)
    expected = expected_database(case)
    for quote, report in zip(allocation.quotes, expected.reports):
        assert quote.model_dump(include=set(Report.model_fields) - {"correction_credit_id"}) == (
            report.model_dump(exclude={"correction_credit_id"})
        )


def test_comparator_does_not_add_omitted_packages_or_call_the_scorer(monkeypatch):
    case = make_case(404)
    expected = expected_database(case)
    candidates = [PackageSelection(account_id=account.account_id) for account in case.savings]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("The tool must not call the evaluator.")

    monkeypatch.setattr("banking.expected_database", forbidden)
    result = compare_packages(case, candidates)
    assert isinstance(result, AllocationQuote)
    assert result.total_annual_bonus == 0
    assert all(q.checking_account_id is None and q.card_id is None for q in result.quotes)
    assert [q.expected_interest for q in result.quotes] != [
        report.expected_interest for report in expected.reports
    ]


def test_comparator_rejects_missing_unknown_forbidden_and_reused_sources():
    case = make_case(404)
    assert isinstance(compare_packages(case, []), QuoteRefusal)
    assert isinstance(
        compare_packages(case, [PackageSelection(account_id="unknown")]), QuoteRefusal
    )
    excluded = PackageSelection(
        account_id=case.savings[0].account_id,
        checking_account_id=case.checking[1].account_id,
        card_id=case.cards[1].card_id,
    )
    refusal = compare_packages(case, [excluded])
    assert isinstance(refusal, QuoteRefusal)
    assert all(
        identifier in refusal.reason
        for identifier in [excluded.account_id, excluded.checking_account_id, excluded.card_id]
    )
    reused = [
        PackageSelection(
            account_id=account.account_id, checking_account_id=case.checking[0].account_id
        )
        for account in case.savings
    ]
    assert isinstance(compare_packages(case, reused), QuoteRefusal)


def test_duplicate_candidates_do_not_change_the_comparison():
    case = make_case(404)
    candidates = permitted_packages(case)
    assert compare_packages(case, candidates) == compare_packages(case, candidates * 2)


def test_typed_tools_save_the_exact_database_without_manual_report_fields(tmp_path):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from runner import episode

    case = make_case(404)
    expected = expected_database(case)
    calls = [
        ToolCallPart(
            "compare_packages",
            {"packages": [package.model_dump(mode="json") for package in permitted_packages(case)]},
            "comparison",
        )
    ]
    for index, (credit, report) in enumerate(zip(expected.credits, expected.reports)):
        calls.append(
            ToolCallPart(
                "credit_interest",
                {"account_id": credit.account_id},
                f"credit-{index}",
            )
        )
        calls.append(
            ToolCallPart(
                "file_discrepancy_report",
                {
                    "account_id": report.account_id,
                    "correction_credit_id": credit.credit_id,
                },
                f"report-{index}",
            )
        )
    steps = iter(calls)

    def respond(_messages, _info):
        call = next(steps, None)
        if call is None:
            return ModelResponse(
                [TextPart("The banking actions are complete.")], finish_reason="stop"
            )
        return ModelResponse([call], finish_reason="tool_call")

    result = asyncio.run(
        episode(tmp_path / "episode", case, "test", "none", test_model=FunctionModel(respond))
    )
    assert result["passed"]
    assert result["validation_corrections"] == 0
    records = json.loads((tmp_path / "episode" / "tool-calls.json").read_text())
    reports = [call for call in records if call["name"] == "file_discrepancy_report"]
    assert len(reports) == 4
    assert all(call["result"]["saved"] for call in reports)
    assert all(set(call["arguments"]) == {"account_id", "correction_credit_id"} for call in reports)


@pytest.mark.parametrize("account_index", [0, 1, 2, 3])
def test_report_fields_are_derived_from_the_selected_quote(account_index):
    case = make_case(404)
    expected = expected_database(case)
    report = expected.reports[account_index]
    credit = expected.credits[account_index]
    request = ReportRequest(**report.model_dump(include=set(ReportRequest.model_fields)))
    assert report_from_quote(case, request, credit) == report


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", Decimal("0.01")),
        ("account_id", "unknown"),
        ("statement_month", "2030-01"),
        ("credit_id", "other"),
        ("reason_code", "other"),
    ],
)
def test_report_rejects_a_mismatched_credit(field, value):
    case = make_case(404)
    expected = expected_database(case)
    request = ReportRequest(
        **expected.reports[0].model_dump(include=set(ReportRequest.model_fields))
    )
    credit = expected.credits[0].model_copy(update={field: value})
    assert isinstance(report_from_quote(case, request, credit), QuoteRefusal)
