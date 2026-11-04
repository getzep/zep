from __future__ import annotations

import ast
import json
import re
import sys
from decimal import Decimal
from enum import Enum
from pathlib import Path

PYTHON_BANKING = Path(__file__).resolve().parents[3] / "python" / "agent-skills-banking"
sys.path.insert(0, str(PYTHON_BANKING))

from banking import (  # noqa: E402
    PackageSelection,
    allocation_rank,
    compare_packages,
    expected_database,
    InterestEntry,
    make_case,
    quote_package,
    score,
)


def dump(value: object) -> object:
    if hasattr(value, "model_dump"):
        return dump(value.model_dump(mode="json"))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [dump(item) for item in value]
    if isinstance(value, tuple):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        return {key: dump(item) for key, item in value.items()}
    return value


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    prompt_source = (root / "src" / "prompts.ts").read_text()

    def read_prompt(name: str) -> str:
        match = re.search(rf"export const {name} = \(\n(.*?)\n\);", prompt_source, re.S)
        if not match:
            raise ValueError(f"Could not read prompt {name}.")
        parts = [
            ast.literal_eval(line.strip().removesuffix("+").strip())
            for line in match[1].splitlines()
        ]
        return "".join(parts)

    seeds = [*range(12), 22, 10134]
    rows = []
    for seed in seeds:
        case = make_case(seed)
        database = expected_database(case)
        reports = {item.account_id: item for item in database.reports}
        best = [
            PackageSelection(
                account_id=account.account_id,
                checking_account_id=reports[account.account_id].checking_account_id
                if account.account_id in reports
                else None,
                card_id=reports[account.account_id].card_id
                if account.account_id in reports
                else None,
            )
            for account in case.savings
        ]
        first = case.savings[0]
        unboosted = PackageSelection(account_id=first.account_id)
        single_source = PackageSelection(
            account_id=first.account_id,
            checking_account_id=case.checking[0].account_id,
        )
        refusal = PackageSelection(
            account_id=first.account_id,
            checking_account_id="not-a-checking-account",
        )
        comparison_inputs = [*best, unboosted, single_source]
        rows.append(
            {
                "seed": seed,
                "case": dump(case),
                "expected_database": dump(database),
                "score": dump(score(case, database)),
                "quotes": {
                    "best": dump(quote_package(case, **best[0].model_dump())),
                    "unboosted": dump(quote_package(case, **unboosted.model_dump())),
                    "single_source": dump(quote_package(case, **single_source.model_dump())),
                    "refusal": dump(quote_package(case, **refusal.model_dump())),
                },
                "comparison": dump(compare_packages(case, comparison_inputs)),
                "comparison_refusal": dump(compare_packages(case, [*best, refusal])),
                "comparison_inputs": dump(comparison_inputs),
            }
        )
    tie_source = make_case(0)
    tie_accounts = sorted(tie_source.savings, key=lambda account: account.account_id)[:2]
    tie_checking = tie_source.checking[0].model_copy(
        update={
            "product": "Premier",
            "active": True,
            "payroll": Decimal("1500"),
            "debit_purchases": 10,
        }
    )
    tie_card = tie_source.cards[0].model_copy(
        update={
            "product": "Travel",
            "open": True,
            "delinquent": False,
            "purchases": Decimal("500"),
        }
    )
    tie_case = tie_source.model_copy(
        update={
            "savings": [
                account.model_copy(
                    update={"product": "Willow", "statement_balance": Decimal("5000.00")}
                )
                for account in tie_accounts
            ],
            "checking": [tie_checking],
            "cards": [tie_card],
            "interest_entries": [
                InterestEntry(
                    account_id=account.account_id,
                    statement_month=tie_source.statement_month,
                    kind="interest",
                    amount=Decimal("0.00"),
                )
                for account in tie_accounts
            ],
        }
    )
    first, second = tie_case.savings
    pair_inputs = [
        PackageSelection(
            account_id=first.account_id,
            checking_account_id=tie_checking.account_id,
            card_id=tie_card.card_id,
        ),
        PackageSelection(account_id=second.account_id),
    ]
    split_inputs = [
        PackageSelection(account_id=first.account_id, card_id=tie_card.card_id),
        PackageSelection(account_id=second.account_id, checking_account_id=tie_checking.account_id),
    ]
    pair_result = compare_packages(tie_case, pair_inputs)
    split_result = compare_packages(tie_case, split_inputs)
    if not pair_result.permitted or not split_result.permitted:
        raise AssertionError("The allocation tie case must have two permitted allocations.")
    if pair_result.total_annual_bonus != split_result.total_annual_bonus:
        raise AssertionError("The allocation tie case must have equal annual bonuses.")
    if allocation_rank(pair_result) == allocation_rank(split_result):
        raise AssertionError("The allocation tie case must have different tie tuples.")
    tie_comparison_inputs = [*pair_inputs[:1], *split_inputs[:1], *pair_inputs[1:], *split_inputs[1:]]
    tie_comparison_reverse_inputs = list(reversed(tie_comparison_inputs))
    tie_comparison = compare_packages(tie_case, tie_comparison_inputs)
    tie_comparison_reverse = compare_packages(tie_case, tie_comparison_reverse_inputs)
    if (
        not tie_comparison.permitted
        or not tie_comparison_reverse.permitted
        or allocation_rank(tie_comparison) != allocation_rank(pair_result)
        or allocation_rank(tie_comparison_reverse) != allocation_rank(pair_result)
    ):
        raise AssertionError("Both submission orders must select the higher allocation rank.")
    tie_row = {
        "case": dump(tie_case),
        "expected_database": dump(expected_database(tie_case)),
        "pair_inputs": dump(pair_inputs),
        "pair_comparison": dump(pair_result),
        "split_inputs": dump(split_inputs),
        "split_comparison": dump(split_result),
        "comparison_inputs": dump(tie_comparison_inputs),
        "comparison": dump(tie_comparison),
        "comparison_reverse_inputs": dump(tie_comparison_reverse_inputs),
        "comparison_reverse": dump(tie_comparison_reverse),
    }
    output = root / "test" / "golden" / "banking.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    prompts = {name: read_prompt(name) for name in ("SYSTEM", "USER", "TEACHING_REQUEST")}
    output.write_text(json.dumps({"prompts": prompts, "cases": rows, "allocation_tie": tie_row}, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
