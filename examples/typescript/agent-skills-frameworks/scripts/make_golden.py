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
    compare_packages,
    expected_database,
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
    output = root / "test" / "golden" / "banking.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    prompts = {name: read_prompt(name) for name in ("SYSTEM", "USER", "TEACHING_REQUEST")}
    output.write_text(json.dumps({"prompts": prompts, "cases": rows}, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
