from pathlib import Path
from typing import Any

from banking import PackageSelection, expected_database, make_case, score
from tools import BankingTools


def test_banking_copy_matches_source() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root.parent / "agent-skills-banking" / "banking.py"
    assert (root / "banking.py").read_bytes() == source.read_bytes()


def test_scripted_tool_sequence_matches_expected_database() -> None:
    case = make_case(10134)
    expected = expected_database(case)
    tools = BankingTools(case)
    selections = [
        PackageSelection(
            account_id=report.account_id,
            checking_account_id=report.checking_account_id,
            card_id=report.card_id,
        )
        for report in expected.reports
    ]
    operations: list[tuple[str, dict[str, Any]]] = [("compare_packages", {"packages": selections})]
    expected_calls = ["compare_packages"]
    for credit in expected.credits:
        operations.extend(
            [
                ("credit_interest", {"account_id": credit.account_id}),
                (
                    "file_discrepancy_report",
                    {
                        "account_id": credit.account_id,
                        "correction_credit_id": credit.credit_id,
                    },
                ),
            ]
        )
        expected_calls.extend(["credit_interest", "file_discrepancy_report"])
    for name, arguments in operations:
        getattr(tools, name)(**arguments)
    assert score(case, tools.database)["passed"]
    assert [call["name"] for call in tools.calls] == expected_calls
