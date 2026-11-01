"""One PydanticAI loop, the same local tools, and a retrieved live Skill."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, cast

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import Agent, ModelRetry, capture_run_messages
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from audit import AuditedTransport
from banking import (
    DOCUMENTS,
    AllocationQuote,
    Case,
    Database,
    PackageSelection,
    QuoteRefusal,
    ReportRequest,
    allocation_rank,
    calculate,
    credit_from_quote,
    make_case,
    quote_package,
    report_from_quote,
    score,
)
from banking import (
    compare_packages as compare_submitted_packages,
)

DEFAULT_STUDENT_MODEL = "claude-haiku-5-5"
REQUEST_TIMEOUT = 600
MAX_OUTPUT_TOKENS = 32768
NORMAL_PROVIDER_FINISH_REASONS = {
    "anthropic": {"stop": "end_turn", "tool_call": "tool_use"},
    "openai": {"stop": "completed", "tool_call": "completed"},
}

SYSTEM = (
    "You are a banking service agent. Resolve the customer's request using the local "
    "banking tools and the bank's policies. The customer is already verified and has "
    "authorized the necessary correction credits and reports. Do not ask for more input. "
    "Use the records and policy evidence instead of inventing account facts or rates. "
    "Give a short final summary of the actions and calculations. "
    "If a relevant Skill is in the catalog, read the Skill before acting. "
    "The Skill catalog is: {catalog}"
)
USER = (
    "Please investigate the savings interest for statement month {month} on all my "
    "savings accounts. Correct each underpayment and save the discrepancy reports. "
    "You have my permission to complete these actions. My case ID is {case_id}."
)
TEACHING_REQUEST = (
    "For this teaching run, keep the action summary short. Then show a comparison table "
    "of every eligible, permitted checking/card package for each savings account, "
    "including its three capped bonus contributions. Submit all permitted packages to "
    "compare_packages, including unboosted and single-source alternatives. This tool "
    "compares complete allocations without reusing sources, but it does not generate "
    "omitted packages. Complete the comparisons before posting any credit. The bank retains "
    "the best quoted allocation. Post each correction using its savings account ID, then "
    "file the report using that account ID and its saved credit ID. "
    "Use quote_interest to evaluate each package instead of reimplementing its arithmetic. "
    "After the tables, document the reusable procedure that another banking agent "
    "can follow on different account records. State the general selection rule with "
    "variables and policy caps. Do not use customer identifiers or case-specific "
    "amounts in the reusable procedure. Use symbolic variables for source identities. "
    "Do not use c1, c2, c3, k1, k2, or k3 as symbolic names. "
    "The reusable procedure must not prescribe fixed product pairings; the winning "
    "allocation depends on each case's balances."
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "audit.py",
            "banking.py",
            "hosted.py",
            "pyproject.toml",
            "runner.py",
            "uv.lock",
            "walkthrough.py",
        )
    }


def model_identifier(model_name: str) -> tuple[str, str]:
    provider, separator, name = model_name.partition(":")
    if not separator:
        provider = ""
        name = model_name
        if name.startswith("claude-"):
            provider = "anthropic"
        elif name.startswith(("gpt-", "o1", "o3", "o4")):
            provider = "openai"
    if provider not in {"anthropic", "openai"} or not name:
        raise ValueError("Use an anthropic:MODEL or openai:MODEL identifier.")
    return provider, name


def build_agent(
    case: Case,
    model: Model,
    effort: str,
    skills: list[dict[str, Any]],
    db: Database,
    calls: list[dict[str, Any]],
) -> Agent:
    best_comparison: AllocationQuote | None = None
    allocation_locked = False
    catalog = [
        {
            "uuid": s["uuid"],
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "version": s["version"],
        }
        for s in skills
    ]
    anthropic_settings = AnthropicModelSettings(
        anthropic_thinking={"type": "disabled"} if effort == "none" else {"type": "adaptive"},
        anthropic_effort=None if effort == "none" else cast(Any, effort),
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=REQUEST_TIMEOUT,
        parallel_tool_calls=False,
    )
    agent = Agent(
        model,
        instructions=SYSTEM.format(catalog=json.dumps(catalog)),
        model_settings=OpenAIResponsesModelSettings(
            openai_reasoning_effort=cast(Any, effort),
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout=REQUEST_TIMEOUT,
            parallel_tool_calls=False,
        )
        if isinstance(model, OpenAIResponsesModel)
        else anthropic_settings,
        retries=2,
    )

    def record(name: str, arguments: dict, result: Any) -> Any:
        calls.append({"name": name, "arguments": arguments, "result": result})
        return result

    @agent.tool_plain
    def get_case() -> dict:
        """Read the case's savings, checking, cards, and interest ledger for all months."""
        return record("get_case", {}, case.model_dump(mode="json"))

    @agent.tool_plain
    def list_documents() -> list[dict[str, str]]:
        """List the titles and IDs of all available bank policy documents."""
        return record(
            "list_documents",
            {},
            [{"id": key, "title": value[0]} for key, value in DOCUMENTS.items()],
        )

    @agent.tool_plain
    def read_document(document_id: str) -> dict:
        """Read the complete bank policy document with this ID."""
        if document_id not in DOCUMENTS:
            raise ModelRetry("The document ID is not in list_documents.")
        title, content = DOCUMENTS[document_id]
        return record(
            "read_document", {"document_id": document_id}, {"title": title, "content": content}
        )

    @agent.tool_plain
    def calculator(expression: str) -> str:
        """Evaluate +, -, *, / and parentheses. Results round half-up to cents: use the full formula in one call."""
        try:
            value = calculate(expression)
        except (ValueError, ArithmeticError, SyntaxError) as error:
            raise ModelRetry(str(error)) from error
        return record("calculator", {"expression": expression}, value)

    @agent.tool_plain
    def quote_interest(
        account_id: str, checking_account_id: str | None = None, card_id: str | None = None
    ) -> dict:
        """Quote statement interest for one proposed checking/card package. This does not post a credit."""
        try:
            quote = quote_package(case, account_id, checking_account_id, card_id)
        except ValueError as error:
            raise ModelRetry(str(error)) from error
        return record(
            "quote_interest",
            {
                "account_id": account_id,
                "checking_account_id": checking_account_id,
                "card_id": card_id,
            },
            quote.model_dump(mode="json"),
        )

    @agent.tool_plain(sequential=True)
    def compare_packages(packages: list[PackageSelection]) -> dict:
        """Compare submitted packages and retain the best complete allocation quoted so far. No omitted packages or unions of separate batches are added. The first saved credit locks the allocation."""
        nonlocal best_comparison
        arguments = {"packages": [package.model_dump(mode="json") for package in packages]}
        if allocation_locked:
            return record(
                "compare_packages",
                arguments,
                QuoteRefusal(reason="The first saved credit has locked the allocation.").model_dump(
                    mode="json"
                ),
            )
        comparison = compare_submitted_packages(case, packages)
        if isinstance(comparison, AllocationQuote):
            if best_comparison is None or allocation_rank(comparison) > allocation_rank(
                best_comparison
            ):
                best_comparison = comparison
            comparison = best_comparison
        return record(
            "compare_packages",
            arguments,
            comparison.model_dump(mode="json"),
        )

    def selected_package(account_id: str) -> PackageSelection | QuoteRefusal:
        if best_comparison is None:
            return QuoteRefusal(reason="Compare packages before posting a correction.")
        quote = next((q for q in best_comparison.quotes if q.account_id == account_id), None)
        if quote is None:
            return QuoteRefusal(reason="The savings account is not in the quoted allocation.")
        return PackageSelection.model_validate(quote.model_dump())

    @agent.tool_plain(sequential=True)
    def credit_interest(account_id: str) -> dict:
        """Post one correction from this account's package in the best quoted allocation. The bank derives the amount. The first saved credit locks the allocation."""
        nonlocal allocation_locked
        arguments = {"account_id": account_id}
        request = selected_package(account_id)
        if isinstance(request, QuoteRefusal):
            return record("credit_interest", arguments, {"saved": False, "reason": request.reason})
        credit = credit_from_quote(case, request)
        if isinstance(credit, QuoteRefusal):
            return record("credit_interest", arguments, {"saved": False, "reason": credit.reason})
        if any(c.credit_id == credit.credit_id for c in db.credits):
            raise ModelRetry("A correction for this account and month already exists.")
        db.credits.append(credit)
        allocation_locked = True
        return record(
            "credit_interest",
            arguments,
            {
                "saved": True,
                "credit": credit.model_dump(mode="json"),
                "package": request.model_dump(mode="json"),
            },
        )

    @agent.tool_plain(sequential=True)
    def file_discrepancy_report(account_id: str, correction_credit_id: str) -> dict:
        """Save this account's report from the locked allocation and its saved correction credit. The bank derives the report fields."""
        arguments = {"account_id": account_id, "correction_credit_id": correction_credit_id}
        selection = selected_package(account_id)
        if isinstance(selection, QuoteRefusal):
            return record(
                "file_discrepancy_report", arguments, {"saved": False, "reason": selection.reason}
            )
        request = ReportRequest(**selection.model_dump(), correction_credit_id=correction_credit_id)
        credit = next((c for c in db.credits if c.credit_id == correction_credit_id), None)
        if credit is None:
            return record(
                "file_discrepancy_report",
                arguments,
                {"saved": False, "reason": "The report must refer to a saved correction credit."},
            )
        report = report_from_quote(case, request, credit)
        if isinstance(report, QuoteRefusal):
            return record(
                "file_discrepancy_report", arguments, {"saved": False, "reason": report.reason}
            )
        if any(
            r.account_id == report.account_id and r.statement_month == report.statement_month
            for r in db.reports
        ):
            raise ModelRetry("A report already exists for this account and month.")
        db.reports.append(report)
        return record(
            "file_discrepancy_report",
            arguments,
            {"saved": True, "report": report.model_dump(mode="json")},
        )

    @agent.tool_plain
    def read_skill(skill_uuid: str) -> dict:
        """Read the complete Markdown of a Skill from the supplied live search catalog."""
        skill = next((s for s in skills if s["uuid"] == skill_uuid), None)
        if skill is None:
            raise ModelRetry("The Skill UUID is not in the current catalog.")
        return record(
            "read_skill",
            {"skill_uuid": skill_uuid},
            {"uuid": skill_uuid, "version": skill["version"], "markdown": skill["markdown"]},
        )

    return agent


async def episode(
    path: Path,
    case: Case,
    model_name: str = DEFAULT_STUDENT_MODEL,
    effort: str = "low",
    skills: list[dict[str, Any]] | None = None,
    test_model: Model | None = None,
    teaching: bool = False,
) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=False)
    skills = skills or []
    db, provider_calls, tool_calls = Database(), [], []
    write_json(
        path / "start.json",
        {
            "case": case.model_dump(mode="json"),
            "model": model_name,
            "effort": effort,
            "teaching": teaching,
            "source_sha256": source_hashes(),
            "skill_identity": [
                {
                    "uuid": s["uuid"],
                    "version": s["version"],
                    "sha256": hashlib.sha256(s["markdown"].encode()).hexdigest(),
                }
                for s in skills
            ],
        },
    )
    client = httpx.AsyncClient(
        transport=AuditedTransport(provider_calls, "agent"), timeout=REQUEST_TIMEOUT
    )
    transcript = []
    try:
        if test_model is not None:
            model = test_model
        else:
            provider, name = model_identifier(model_name)
            if provider == "openai":
                model = OpenAIResponsesModel(
                    name,
                    provider=OpenAIProvider(
                        openai_client=AsyncOpenAI(
                            max_retries=0, timeout=REQUEST_TIMEOUT, http_client=cast(Any, client)
                        )
                    ),
                )
            else:
                model = AnthropicModel(
                    name,
                    provider=AnthropicProvider(
                        anthropic_client=AsyncAnthropic(
                            max_retries=0, timeout=REQUEST_TIMEOUT, http_client=client
                        )
                    ),
                )
        agent = build_agent(case, model, effort, skills, db, tool_calls)
        write_json(path / "settings.json", agent.model_settings)
        started = time.monotonic()
        async with asyncio.timeout(900):
            with capture_run_messages() as transcript:
                result = await agent.run(
                    USER.format(month=case.statement_month, case_id=case.case_id)
                    + ("\n\n" + TEACHING_REQUEST if teaching else ""),
                    usage_limits=UsageLimits(request_limit=150, total_tokens_limit=3000000),
                )
        elapsed = time.monotonic() - started
        transcript = result.all_messages()
        (path / "messages.json").write_bytes(
            ModelMessagesTypeAdapter.dump_json(transcript, indent=2)
        )
        usage = result.usage
        finish_reason = result.response.finish_reason
        responses = [message for message in transcript if isinstance(message, ModelResponse)]
        response_finish_reasons = [message.finish_reason for message in responses]
        write_json(
            path / "completion.json",
            {
                "finish_reason": finish_reason,
                "response_finish_reasons": response_finish_reasons,
                "provider_finish_reasons": [
                    (message.provider_details or {}).get("finish_reason") for message in responses
                ],
                "provider_details": result.response.provider_details,
            },
        )
        if (
            finish_reason != "stop"
            or any(reason not in {"stop", "tool_call"} for reason in response_finish_reasons)
            or any(
                message.provider_name in NORMAL_PROVIDER_FINISH_REASONS
                and (message.provider_details or {}).get("finish_reason")
                != NORMAL_PROVIDER_FINISH_REASONS[message.provider_name].get(
                    message.finish_reason or ""
                )
                for message in responses
            )
        ):
            raise RuntimeError(
                f"The agent response is incomplete: finish_reason={finish_reason}; "
                f"response_finish_reasons={response_finish_reasons}."
            )
        if not result.output.strip():
            raise RuntimeError("The agent returned an empty final answer.")
        if any(c["status"] != 200 for c in provider_calls):
            raise RuntimeError("A provider call failed.")
        if test_model is None:
            input_count = sum(
                c["usage"].get("input_tokens", 0)
                + c["usage"].get("cache_read_input_tokens", 0)
                + c["usage"].get("cache_creation_input_tokens", 0)
                for c in provider_calls
            )
            output_count = sum(c["usage"].get("output_tokens", 0) for c in provider_calls)
            if (len(provider_calls), input_count, output_count) != (
                usage.requests,
                usage.input_tokens,
                usage.output_tokens,
            ):
                raise RuntimeError("Provider request or token accounting did not reconcile.")
        returns = [p for m in transcript for p in m.parts if isinstance(p, ToolReturnPart)]
        if len(returns) != len(tool_calls):
            raise RuntimeError("Tool return accounting did not reconcile.")
        validation = [p for m in transcript for p in m.parts if isinstance(p, RetryPromptPart)]
        reads = [c["result"] for c in tool_calls if c["name"] == "read_skill"]
        if skills and not reads:
            raise RuntimeError("The agent did not read a live Skill.")
        evaluation = score(case, db)
        row = {
            "case_id": case.case_id,
            "model": model_name,
            "effort": effort,
            "passed": evaluation["passed"],
            "tokens": usage.total_tokens,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "seconds": elapsed,
            "requests": usage.requests,
            "provider_calls": len(provider_calls),
            "failed_provider_calls": 0,
            "validation_corrections": len(validation),
            "skill_reads": [{"uuid": s["uuid"], "version": s["version"]} for s in reads],
            "termination": "final_answer",
            "finish_reason": finish_reason,
            "final_answer": result.output,
        }
        write_json(path / "evaluation.json", evaluation)
        write_json(path / "result.json", row)
        print(
            f"{path.name}: {'PASS' if row['passed'] else 'FAIL'}; "
            f"{row['tokens']:,} tokens; {elapsed:.1f}s",
            flush=True,
        )
        return row
    except BaseException as error:
        write_json(path / "failure.json", {"type": type(error).__name__, "error": str(error)})
        raise
    finally:
        (path / "messages.json").write_bytes(
            ModelMessagesTypeAdapter.dump_json(transcript, indent=2)
        )
        write_json(path / "provider-calls.json", provider_calls)
        write_json(path / "tool-calls.json", tool_calls)
        write_json(path / "database.json", db.model_dump(mode="json"))
        await client.aclose()


def trace_events(path: Path) -> list[dict[str, Any]]:
    messages = ModelMessagesTypeAdapter.validate_json((path / "messages.json").read_text())
    events = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                events.append(
                    {
                        "event_type": "tool_call",
                        "content": part.args_as_json_str(),
                        "tool_call_id": part.tool_call_id,
                        "context": {"tools": [{"name": part.tool_name}]},
                    }
                )
            elif isinstance(part, ToolReturnPart):
                events.append(
                    {
                        "event_type": "tool_result",
                        "content": json.dumps(part.content),
                        "tool_call_id": part.tool_call_id,
                        "context": {"tools": [{"name": part.tool_name}]},
                    }
                )
            elif isinstance(part, RetryPromptPart):
                events.append(
                    {
                        "event_type": "tool_result",
                        "tool_call_id": part.tool_call_id,
                        "content": part.model_response(),
                        "context": {"tools": [{"name": part.tool_name or "validation"}]},
                    }
                )
            elif isinstance(part, (TextPart, UserPromptPart)) and str(part.content).strip():
                events.append(
                    {
                        "event_type": "decision" if part.part_kind == "text" else "input_reference",
                        "content": str(part.content),
                    }
                )
    return events


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--model", default=DEFAULT_STUDENT_MODEL)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--skills-json")
    args = parser.parse_args()
    skills = json.loads(Path(args.skills_json).read_text()) if args.skills_json else []
    asyncio.run(episode(Path(args.output), make_case(args.seed), args.model, args.effort, skills))
