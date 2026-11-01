import asyncio
import json
from argparse import Namespace
from contextlib import nullcontext
from decimal import Decimal
from itertools import product

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from banking import (
    AllocationQuote,
    Database,
    PackageQuote,
    PackageSelection,
    QuoteRefusal,
    expected_database,
    make_case,
    quote_allocation,
    quote_package,
    score,
)
from runner import MAX_OUTPUT_TOKENS, REQUEST_TIMEOUT, build_agent, episode


def test_shared_provider_request_timeout():
    models = [
        TestModel(),
        OpenAIResponsesModel("test", provider=OpenAIProvider(api_key="not-used")),
    ]
    for model in models:
        agent = build_agent(make_case(404), model, "none", [], Database(), [])
        settings = agent.model_settings
        assert isinstance(settings, dict)
        assert settings.get("timeout") == REQUEST_TIMEOUT == 600
        assert settings.get("max_tokens") == MAX_OUTPUT_TOKENS == 32768


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error", None])
def test_incomplete_text_cannot_be_saved_as_a_completed_episode(tmp_path, finish_reason):
    def respond(_messages, _info):
        return ModelResponse([TextPart("Incomplete final text")], finish_reason=finish_reason)

    output = tmp_path / "episode"
    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(
            episode(output, make_case(404), "test", "none", test_model=FunctionModel(respond))
        )
    assert not (output / "result.json").exists()
    assert (output / "failure.json").exists()
    assert (output / "messages.json").exists()
    assert (output / "database.json").exists()
    assert json.loads((output / "completion.json").read_text())["finish_reason"] == finish_reason


def test_normal_text_response_records_its_finish_reason(tmp_path):
    def respond(_messages, _info):
        return ModelResponse([TextPart("Complete final text")], finish_reason="stop")

    result = asyncio.run(
        episode(
            tmp_path / "episode", make_case(404), "test", "none", test_model=FunctionModel(respond)
        )
    )
    assert result["finish_reason"] == "stop"
    assert result["termination"] == "final_answer"


@pytest.mark.parametrize(
    "provider,raw_reason",
    [
        ("anthropic", "max_tokens"),
        ("anthropic", "tool_use"),
        ("openai", "incomplete"),
        ("openai", None),
    ],
)
def test_provider_finish_reason_must_match_normalized_stop(tmp_path, provider, raw_reason):
    def respond(_messages, _info):
        return ModelResponse(
            [TextPart("Final text")],
            finish_reason="stop",
            provider_name=provider,
            provider_details={"finish_reason": raw_reason},
        )

    output = tmp_path / "episode"
    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(
            episode(output, make_case(404), "test", "none", test_model=FunctionModel(respond))
        )
    assert not (output / "result.json").exists()


def test_resume_rejects_a_saved_truncated_episode(tmp_path):
    import walkthrough

    path = tmp_path / "teacher"
    path.mkdir()
    (path / "result.json").write_text(
        json.dumps({"passed": True, "termination": "final_answer", "finish_reason": "length"})
    )
    with pytest.raises(RuntimeError, match="did not finish normally"):
        asyncio.run(
            walkthrough.stage(tmp_path, "teacher", 22, "test", "none", walkthrough.source_hashes())
        )


def test_truncated_tool_response_is_rejected_even_after_a_normal_final_response(tmp_path):
    def respond(messages, _info):
        if len(messages) == 1:
            return ModelResponse([ToolCallPart("get_case", {})], finish_reason="length")
        return ModelResponse([TextPart("Complete final text")], finish_reason="stop")

    output = tmp_path / "episode"
    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(
            episode(output, make_case(404), "test", "none", test_model=FunctionModel(respond))
        )
    assert not (output / "result.json").exists()
    completion = json.loads((output / "completion.json").read_text())
    assert completion["finish_reason"] == "stop"
    assert completion["response_finish_reasons"] == ["length", "stop"]


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error", "tool_call", None])
def test_teacher_must_finish_before_any_agent_or_trace_is_created(
    tmp_path, monkeypatch, finish_reason
):
    import walkthrough

    async def fake_stage(_output, name, *_args, **_kwargs):
        return {
            "passed": name == "teacher",
            "termination": "final_answer",
            "finish_reason": finish_reason if name == "teacher" else "stop",
        }

    monkeypatch.setattr(walkthrough, "stage", fake_stage)
    monkeypatch.setattr(walkthrough, "Hosted", lambda _ledger: nullcontext(object()))
    args = Namespace(
        output=str(tmp_path / "learning"),
        resume=False,
        student="claude-haiku-5-5",
        effort="none",
        teacher="claude-opus-5-5",
        teacher_effort="max",
        seed=404,
        teacher_seed=22,
        compile_timeout=900,
        stop_after_skill=True,
    )
    with pytest.raises(RuntimeError, match="No successful Trace will be uploaded"):
        asyncio.run(walkthrough.run_demo(args))
    assert not (tmp_path / "learning" / "agent.json").exists()
    assert not (tmp_path / "learning" / "upload-start.json").exists()


def test_default_student_case_requires_empty_single_and_paired_packages():
    case = make_case(180)
    database = expected_database(case)
    assert [credit.amount for credit in database.credits] == [
        Decimal("18.32"),
        Decimal("28.82"),
        Decimal("63.19"),
        Decimal("19.76"),
    ]
    assert [report.expected_interest for report in database.reports] == [
        Decimal("25.46"),
        Decimal("35.07"),
        Decimal("67.68"),
        Decimal("27.69"),
    ]
    assert [report.checking_account_id for report in database.reports] == [
        None,
        None,
        "case-000180-c1",
        "case-000180-c3",
    ]
    assert [report.card_id for report in database.reports] == [
        "case-000180-k3",
        None,
        "case-000180-k1",
        None,
    ]
    assert score(case, database)["passed"]


def test_default_student_allocation_has_a_unique_bonus_total():
    case = make_case(180)
    options = []
    for account in case.savings:
        quotes = [
            quote_package(case, account.account_id, checking, card)
            for checking in [None] + [source.account_id for source in case.checking]
            for card in [None] + [source.card_id for source in case.cards]
        ]
        options.append([quote for quote in quotes if isinstance(quote, PackageQuote)])
    totals = []
    for allocation in product(*options):
        checking = [quote.checking_account_id for quote in allocation if quote.checking_account_id]
        cards = [quote.card_id for quote in allocation if quote.card_id]
        if len(checking) == len(set(checking)) and len(cards) == len(set(cards)):
            totals.append(sum(quote.annual_bonus for quote in allocation))
    totals.sort(reverse=True)
    assert totals[0] - totals[1] == Decimal("7.35251")


def test_manual_joint_allocation():
    case = make_case(404)
    db = expected_database(case)
    assert [credit.amount for credit in db.credits] == [
        Decimal("16.42"),
        Decimal("82.79"),
        Decimal("18.49"),
        Decimal("62.78"),
    ]
    assert [report.checking_account_id for report in db.reports] == [
        "case-000404-c3",
        None,
        "case-000404-c1",
        None,
    ]
    assert [report.card_id for report in db.reports] == [
        None,
        "case-000404-k1",
        None,
        "case-000404-k3",
    ]
    assert [report.package_bonus_percent for report in db.reports] == [
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    ]
    for report, credit, account in zip(db.reports, db.credits, case.savings, strict=True):
        annual = (
            account.statement_balance * report.base_rate_percent
            + min(account.statement_balance, Decimal(5000)) * report.checking_bonus_percent
            + min(account.statement_balance, Decimal(10000)) * report.card_bonus_percent
            + min(account.statement_balance, Decimal(7500)) * report.package_bonus_percent
        )
        assert abs(annual / 100 / 12 - report.expected_interest) <= Decimal("0.005")
        assert report.expected_interest - report.net_interest_before_run == credit.amount
    assert score(case, db)["passed"]
    wrong = db.model_copy(deep=True)
    wrong.reports[0].checking_account_id = wrong.reports[2].checking_account_id
    assert not score(case, wrong)["passed"]
    wrong = db.model_copy(deep=True)
    wrong.reports[0].expected_interest += Decimal("1.00")
    assert not score(case, wrong)["passed"]


def test_reuse_is_refused_without_selecting_an_alternative():
    case = make_case(0)
    result = quote_allocation(
        case,
        [
            PackageSelection(
                account_id=a.account_id, checking_account_id=case.checking[1].account_id
            )
            for a in case.savings
        ],
    )
    assert isinstance(result, QuoteRefusal)
    result = quote_allocation(
        case, [PackageSelection(account_id=a.account_id) for a in case.savings]
    )
    assert isinstance(result, AllocationQuote)
    assert result.total_annual_bonus == 0


def test_independent_scorer_matches_all_feasible_quoted_allocations():
    for seed in [0, 1, 2, 22, 54, 404, 1009]:
        case = make_case(seed)
        options = []
        for account in case.savings:
            quotes = []
            for checking, card in product(
                [None] + [c.account_id for c in case.checking],
                [None] + [c.card_id for c in case.cards],
            ):
                quote = quote_package(case, account.account_id, checking, card)
                if isinstance(quote, PackageQuote):
                    quotes.append(quote)
            options.append(quotes)
        feasible = []
        for allocation in product(*options):
            proposal = [
                PackageSelection(
                    **q.model_dump(include={"account_id", "checking_account_id", "card_id"})
                )
                for q in allocation
            ]
            result = quote_allocation(case, proposal)
            if isinstance(result, AllocationQuote):
                feasible.append(result)
        best = max(
            feasible,
            key=lambda q: (
                q.total_annual_bonus,
                tuple(
                    v
                    for item in q.quotes
                    for v in (item.card_bonus_percent, item.checking_bonus_percent)
                ),
            ),
        )
        expected = expected_database(case)
        for quote, report in zip(best.quotes, expected.reports, strict=True):
            assert quote.model_dump(
                include=set(type(report).model_fields) - {"correction_credit_id"}
            ) == report.model_dump(exclude={"correction_credit_id"})


def test_teacher_has_different_optimal_product_allocation():
    from walkthrough import allocation_signature

    assert allocation_signature(22) != allocation_signature(404)
    assert allocation_signature(1009) != allocation_signature(404)
    assert allocation_signature(54) == allocation_signature(1009)


def test_teacher_case_leaves_two_accounts_unboosted():
    case = make_case(22)
    db = expected_database(case)
    assert [(r.checking_account_id, r.card_id) for r in db.reports] == [
        (case.checking[0].account_id, case.cards[1].card_id),
        (None, None),
        (None, None),
        (case.checking[1].account_id, case.cards[0].card_id),
    ]
    willow = case.savings[3]
    quote = quote_package(
        case, willow.account_id, case.checking[1].account_id, case.cards[0].card_id
    )
    assert isinstance(quote, PackageQuote)
    annual_bonus = (Decimal(5000) * Decimal("1.00") + Decimal(10000) * Decimal("0.90")) / 100
    assert quote.annual_bonus == annual_bonus == Decimal(140)
    assert quote.expected_interest == Decimal("76.20")
    assert [r.expected_interest for r in db.reports] == list(
        map(Decimal, ["28.71", "48.86", "20.30", "76.20"])
    )


def test_student_case_requires_four_single_source_packages():
    case = make_case(1)
    db = expected_database(case)
    assert [(r.checking_account_id, r.card_id) for r in db.reports] == [
        (case.checking[1].account_id, None),
        (case.checking[0].account_id, None),
        (None, case.cards[1].card_id),
        (None, case.cards[0].card_id),
    ]
    assert [r.expected_interest for r in db.reports] == list(
        map(Decimal, ["22.17", "78.83", "73.69", "82.31"])
    )
    assert [credit.amount for credit in db.credits] == list(
        map(Decimal, ["17.85", "70.99", "67.66", "78.11"])
    )


def test_request_covers_all_accounts_in_the_case():
    from runner import USER

    case = make_case(1009)
    request = USER.format(month=case.statement_month, case_id=case.case_id)
    assert "all my savings accounts" in request
    assert "three" not in request


def test_willow_package_exclusion_comes_from_the_relationship_policy():
    case = make_case(404)
    card = case.cards[0].model_copy(update={"product": "Platinum"})
    case.cards[0] = card
    refused = quote_package(
        case, case.savings[3].account_id, case.checking[1].account_id, card.card_id
    )
    assert isinstance(refused, QuoteRefusal)
    single = quote_package(case, case.savings[3].account_id, card_id=card.card_id)
    assert isinstance(single, PackageQuote)
    assert single.card_bonus_percent == Decimal("0.65")


def test_leakage_includes_checking_cards_and_short_source_aliases():
    from walkthrough import check_leakage

    audit = check_leakage(
        [{"markdown": "case-000404-c3 and case-000404-k3; use c3 with k3."}], [404]
    )
    assert {hit["value"] for hit in audit["exact_heldout_identifiers_balances_or_corrections"]} == {
        "case-000404",
        "case-000404-c3",
        "case-000404-k3",
    }
    assert audit["source_id_suffixes"] == ["c3", "k3"]


def test_matching_teacher_allocation_is_rejected_before_service_calls(tmp_path):
    import argparse
    import asyncio

    from walkthrough import run_demo

    args = argparse.Namespace(
        output=str(tmp_path / "invalid-run"),
        resume=False,
        student="claude-haiku-5-5",
        effort="none",
        teacher="claude-opus-5-5",
        teacher_effort="max",
        seed=1009,
        teacher_seed=54,
    )
    with pytest.raises(ValueError, match="different optimal product allocations"):
        asyncio.run(run_demo(args))


def test_null_package_keeps_the_documented_boosted_balance():
    case = make_case(22)
    for account in case.savings:
        quote = quote_package(case, account.account_id, None, None)
        assert isinstance(quote, PackageQuote)
        assert quote.package_bonus_percent == 0
        assert quote.package_boosted_balance == min(account.statement_balance, Decimal(7500))
        assert quote.checking_boosted_balance == min(account.statement_balance, Decimal(5000))
        assert quote.card_boosted_balance == min(account.statement_balance, Decimal(10000))
