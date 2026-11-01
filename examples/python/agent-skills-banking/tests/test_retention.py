from decimal import Decimal
from itertools import product

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import banking
from banking import (
    AllocationQuote,
    Database,
    InterestEntry,
    PackageQuote,
    PackageSelection,
    allocation_rank,
    compare_packages,
    expected_database,
    make_case,
    quote_package,
    score,
)
from runner import build_agent
from tests.test_runner import scripted_model


def empty_packages(case):
    return [{"account_id": account.account_id} for account in case.savings]


def permitted_packages(case):
    selections = []
    for account, checking, card in product(
        case.savings,
        [None, *(c.account_id for c in case.checking)],
        [None, *(c.card_id for c in case.cards)],
    ):
        selection = PackageSelection(
            account_id=account.account_id, checking_account_id=checking, card_id=card
        )
        if isinstance(quote_package(case, **selection.model_dump()), PackageQuote):
            selections.append(selection.model_dump(mode="json"))
    return selections


def compare(packages):
    return ("compare_packages", {"packages": packages})


def writes(case):
    operations: list[tuple[str, dict]] = []
    for account in case.savings:
        operations.append(("credit_interest", {"account_id": account.account_id}))
        operations.append(
            (
                "file_discrepancy_report",
                {
                    "account_id": account.account_id,
                    "correction_credit_id": f"credit-{account.account_id}-{case.statement_month}",
                },
            )
        )
    return operations


def run(case, operations):
    db, calls = Database(), []
    agent = build_agent(case, scripted_model(operations), "none", [], db, calls)
    agent.run_sync("Run the invented offline tool sequence.")
    return db, calls


@pytest.mark.parametrize("reverse", [False, True])
def test_best_comparison_retained_and_credit_result_discloses_package(reverse):
    case = make_case(180)
    batches = [empty_packages(case), permitted_packages(case)]
    if reverse:
        batches.reverse()
    db, calls = run(case, [*(compare(batch) for batch in batches), *writes(case)])
    assert score(case, db)["passed"]
    assert calls[1]["result"]["total_annual_bonus"] == "265.00"
    assert calls[0]["result"]["total_annual_bonus"] == ("265.00" if reverse else "0.00")
    credits = [c for c in calls if c["name"] == "credit_interest"]
    for call, report in zip(credits, db.reports, strict=True):
        assert call["result"]["package"] == {
            "account_id": report.account_id,
            "checking_account_id": report.checking_account_id,
            "card_id": report.card_id,
        }


def test_separate_batches_are_not_unioned():
    case = make_case(180)
    first, second = empty_packages(case), empty_packages(case)
    first[0]["checking_account_id"] = case.checking[0].account_id
    second[2]["card_id"] = case.cards[0].card_id
    a = compare_packages(case, [PackageSelection(**p) for p in first])
    b = compare_packages(case, [PackageSelection(**p) for p in second])
    union = compare_packages(case, [PackageSelection(**p) for p in [*first, *second]])
    assert isinstance(a, AllocationQuote) and isinstance(b, AllocationQuote)
    assert isinstance(union, AllocationQuote)
    assert union.total_annual_bonus > max(a.total_annual_bonus, b.total_annual_bonus)
    db, calls = run(case, [compare(first), compare(second), *writes(case)])
    assert calls[1]["result"] == max([a, b], key=allocation_rank).model_dump(mode="json")
    assert not score(case, db)["passed"]


def test_equal_rank_keeps_earlier_source_identity():
    case = make_case(180)
    case.checking.append(case.checking[0].model_copy(update={"account_id": "duplicate-product"}))
    first, second = empty_packages(case), empty_packages(case)
    first[0]["checking_account_id"] = case.checking[0].account_id
    second[0]["checking_account_id"] = "duplicate-product"
    a = compare_packages(case, [PackageSelection(**p) for p in first])
    b = compare_packages(case, [PackageSelection(**p) for p in second])
    assert isinstance(a, AllocationQuote) and isinstance(b, AllocationQuote)
    assert a != b and allocation_rank(a) == allocation_rank(b)
    _, calls = run(case, [compare(first), compare(second), *writes(case)])
    assert calls[1]["result"] == a.model_dump(mode="json")
    assert calls[2]["result"]["package"]["checking_account_id"] == case.checking[0].account_id


def test_equal_total_uses_policy_tie_break():
    case = make_case(180)
    # Equal balances and identical products produce an allocation tie across accounts.
    case.savings[1] = case.savings[0].model_copy(update={"account_id": case.savings[1].account_id})
    first, second = empty_packages(case), empty_packages(case)
    first[1]["card_id"] = case.cards[0].card_id
    second[0]["card_id"] = case.cards[0].card_id
    a = compare_packages(case, [PackageSelection(**p) for p in first])
    b = compare_packages(case, [PackageSelection(**p) for p in second])
    assert isinstance(a, AllocationQuote) and isinstance(b, AllocationQuote)
    assert a.total_annual_bonus == b.total_annual_bonus and allocation_rank(b) > allocation_rank(a)
    _, calls = run(case, [compare(first), compare(second)])
    assert calls[1]["result"] == b.model_dump(mode="json")


@pytest.mark.parametrize("invalid", [[], [{"account_id": "missing"}]])
def test_invalid_batch_does_not_clear_retained_allocation(invalid):
    case = make_case(180)
    db, calls = run(case, [compare(permitted_packages(case)), compare(invalid), *writes(case)])
    assert calls[1]["result"]["permitted"] is False
    assert score(case, db)["passed"]


def test_no_comparison_and_unknown_account_refuse_without_locking():
    case = make_case(180)
    credit = ("credit_interest", {"account_id": case.savings[0].account_id})
    db, calls = run(
        case,
        [
            credit,
            compare(empty_packages(case)),
            ("credit_interest", {"account_id": "missing"}),
            compare(permitted_packages(case)),
            *writes(case),
        ],
    )
    assert calls[0]["result"]["saved"] is False
    assert calls[2]["result"]["saved"] is False
    assert calls[3]["result"]["total_annual_bonus"] == "265.00"
    assert score(case, db)["passed"]


def test_no_shortfall_refuses_without_locking():
    case = make_case(180)
    account_id = case.savings[0].account_id
    case.interest_entries.append(
        InterestEntry(
            account_id=account_id,
            statement_month=case.statement_month,
            kind="interest",
            amount=Decimal(100000),
        )
    )
    db, calls = run(
        case,
        [
            compare(empty_packages(case)),
            ("credit_interest", {"account_id": account_id}),
            compare(permitted_packages(case)),
        ],
    )
    assert calls[1]["result"]["saved"] is False
    assert calls[2]["result"]["total_annual_bonus"] == "265.00"
    assert db == Database()


def test_saved_credit_locks_allocation_and_repeated_late_calls_are_refusals():
    case = make_case(180)
    operations = [compare(empty_packages(case)), *writes(case)[:1]]
    operations += [compare(permitted_packages(case))] * 4
    operations += writes(case)[1:]
    db, calls = run(case, operations)
    assert all(call["result"]["permitted"] is False for call in calls[2:6])
    assert all("locked" in call["result"]["reason"] for call in calls[2:6])
    assert len(db.credits) == len(db.reports) == 4
    assert all(r.checking_account_id is None and r.card_id is None for r in db.reports)
    assert not score(case, db)["passed"]


def test_report_requires_matching_credit_without_mutating_state():
    case = make_case(180)
    first, second = case.savings[:2]
    db, calls = run(
        case,
        [
            compare(permitted_packages(case)),
            writes(case)[0],
            (
                "file_discrepancy_report",
                {
                    "account_id": second.account_id,
                    "correction_credit_id": f"credit-{first.account_id}-{case.statement_month}",
                },
            ),
            (
                "file_discrepancy_report",
                {"account_id": first.account_id, "correction_credit_id": "missing"},
            ),
            writes(case)[1],
        ],
    )
    assert calls[2]["result"]["saved"] is False and calls[3]["result"]["saved"] is False
    assert len(db.credits) == len(db.reports) == 1


def test_retention_is_isolated_between_agents():
    case = make_case(180)
    run(case, [compare(permitted_packages(case)), *writes(case)])
    db, calls = run(case, writes(case)[:1])
    assert db == Database() and calls[0]["result"]["saved"] is False


def test_tools_do_not_read_gold_or_scorer(monkeypatch):
    case = make_case(180)
    expected = expected_database(case)
    packages = permitted_packages(case)

    def forbidden(*args, **kwargs):
        raise AssertionError("An agent tool accessed the scorer or expected database.")

    monkeypatch.setattr(banking, "expected_database", forbidden)
    monkeypatch.setattr(banking, "score", forbidden)
    db, _ = run(case, [compare(packages), *writes(case)])
    assert db == expected


def test_batched_tools_run_in_order_without_mixing_allocations():
    case = make_case(180)
    operations = [
        compare(empty_packages(case)),
        *writes(case)[:1],
        compare(permitted_packages(case)),
    ]
    pending = True

    def respond(messages, info):
        nonlocal pending
        if pending:
            pending = False
            return ModelResponse(
                [
                    ToolCallPart(name, arguments, tool_call_id=f"batch-{i}")
                    for i, (name, arguments) in enumerate(operations)
                ],
                finish_reason="tool_call",
            )
        return ModelResponse([TextPart("The offline batch is complete.")], finish_reason="stop")

    db, calls = Database(), []
    agent = build_agent(case, FunctionModel(respond), "none", [], db, calls)
    agent.run_sync("Run the invented offline batch.")
    assert [call["name"] for call in calls] == [name for name, _ in operations]
    assert calls[1]["result"]["saved"] is True
    assert calls[2]["result"]["permitted"] is False
    assert calls[1]["result"]["package"]["card_id"] is None
