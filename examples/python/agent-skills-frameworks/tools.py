"""Framework-neutral tools for the invented banking case."""

from __future__ import annotations

from typing import Any

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
    quote_package,
    report_from_quote,
)
from banking import compare_packages as compare_submitted_packages


class ToolError(Exception):
    """A recoverable tool error that the active framework must return to the model."""


class BankingTools:
    def __init__(self, case: Case, skills: list[dict[str, Any]] | None = None) -> None:
        self.case = case
        self.skills = skills or []
        self.database = Database()
        self.calls: list[dict[str, Any]] = []
        self._best_comparison: AllocationQuote | None = None
        self._allocation_locked = False

    def _record(self, name: str, arguments: dict[str, Any], result: Any) -> Any:
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result

    def get_case(self) -> dict[str, Any]:
        """Read the case's savings, checking, cards, and interest ledger for all months."""
        return self._record("get_case", {}, self.case.model_dump(mode="json"))

    def list_documents(self) -> list[dict[str, str]]:
        """List the titles and IDs of all available bank policy documents."""
        return self._record(
            "list_documents",
            {},
            [{"id": key, "title": value[0]} for key, value in DOCUMENTS.items()],
        )

    def read_document(self, document_id: str) -> dict[str, str]:
        """Read the complete bank policy document with this ID."""
        if document_id not in DOCUMENTS:
            raise ToolError("The document ID is not in list_documents.")
        title, content = DOCUMENTS[document_id]
        return self._record(
            "read_document",
            {"document_id": document_id},
            {"title": title, "content": content},
        )

    def calculator(self, expression: str) -> str:
        """Evaluate +, -, *, / and parentheses. Results round half-up to cents: use the full formula in one call."""
        try:
            result = calculate(expression)
        except (ValueError, ArithmeticError, SyntaxError) as error:
            raise ToolError(str(error)) from error
        return self._record("calculator", {"expression": expression}, result)

    def quote_interest(
        self, account_id: str, checking_account_id: str | None = None, card_id: str | None = None
    ) -> dict[str, Any]:
        """Quote statement interest for one proposed checking/card package. This does not post a credit."""
        try:
            quote = quote_package(self.case, account_id, checking_account_id, card_id)
        except ValueError as error:
            raise ToolError(str(error)) from error
        return self._record(
            "quote_interest",
            {
                "account_id": account_id,
                "checking_account_id": checking_account_id,
                "card_id": card_id,
            },
            quote.model_dump(mode="json"),
        )

    def compare_packages(self, packages: list[PackageSelection]) -> dict[str, Any]:
        """Compare submitted packages and retain the best complete allocation quoted so far. No omitted packages or unions of separate batches are added. The first saved credit locks the allocation."""
        arguments = {"packages": [package.model_dump(mode="json") for package in packages]}
        if self._allocation_locked:
            return self._record(
                "compare_packages",
                arguments,
                QuoteRefusal(reason="The first saved credit has locked the allocation.").model_dump(
                    mode="json"
                ),
            )
        comparison = compare_submitted_packages(self.case, packages)
        if isinstance(comparison, AllocationQuote):
            if self._best_comparison is None or allocation_rank(comparison) > allocation_rank(
                self._best_comparison
            ):
                self._best_comparison = comparison
            comparison = self._best_comparison
        return self._record(
            "compare_packages",
            arguments,
            comparison.model_dump(mode="json"),
        )

    def _selected_package(self, account_id: str) -> PackageSelection | QuoteRefusal:
        if self._best_comparison is None:
            return QuoteRefusal(reason="Compare packages before posting a correction.")
        quote = next(
            (item for item in self._best_comparison.quotes if item.account_id == account_id), None
        )
        if quote is None:
            return QuoteRefusal(reason="The savings account is not in the quoted allocation.")
        return PackageSelection.model_validate(quote.model_dump())

    def credit_interest(self, account_id: str) -> dict[str, Any]:
        """Post one correction from this account's package in the best quoted allocation. The bank derives the amount. The first saved credit locks the allocation."""
        arguments = {"account_id": account_id}
        request = self._selected_package(account_id)
        if isinstance(request, QuoteRefusal):
            return self._record(
                "credit_interest", arguments, {"saved": False, "reason": request.reason}
            )
        credit = credit_from_quote(self.case, request)
        if isinstance(credit, QuoteRefusal):
            return self._record(
                "credit_interest", arguments, {"saved": False, "reason": credit.reason}
            )
        if any(item.credit_id == credit.credit_id for item in self.database.credits):
            raise ToolError("A correction for this account and month already exists.")
        self.database.credits.append(credit)
        self._allocation_locked = True
        return self._record(
            "credit_interest",
            arguments,
            {
                "saved": True,
                "credit": credit.model_dump(mode="json"),
                "package": request.model_dump(mode="json"),
            },
        )

    def file_discrepancy_report(self, account_id: str, correction_credit_id: str) -> dict[str, Any]:
        """Save this account's report from the locked allocation and its saved correction credit. The bank derives the report fields."""
        arguments = {"account_id": account_id, "correction_credit_id": correction_credit_id}
        selection = self._selected_package(account_id)
        if isinstance(selection, QuoteRefusal):
            return self._record(
                "file_discrepancy_report",
                arguments,
                {"saved": False, "reason": selection.reason},
            )
        request = ReportRequest(**selection.model_dump(), correction_credit_id=correction_credit_id)
        credit = next(
            (item for item in self.database.credits if item.credit_id == correction_credit_id), None
        )
        if credit is None:
            return self._record(
                "file_discrepancy_report",
                arguments,
                {"saved": False, "reason": "The report must refer to a saved correction credit."},
            )
        report = report_from_quote(self.case, request, credit)
        if isinstance(report, QuoteRefusal):
            return self._record(
                "file_discrepancy_report", arguments, {"saved": False, "reason": report.reason}
            )
        if any(
            item.account_id == report.account_id and item.statement_month == report.statement_month
            for item in self.database.reports
        ):
            raise ToolError("A report already exists for this account and month.")
        self.database.reports.append(report)
        return self._record(
            "file_discrepancy_report",
            arguments,
            {"saved": True, "report": report.model_dump(mode="json")},
        )

    def read_skill(self, skill_uuid: str) -> dict[str, Any]:
        """Read the complete Markdown of a Skill from the supplied live search catalog."""
        skill = next((item for item in self.skills if item["uuid"] == skill_uuid), None)
        if skill is None:
            raise ToolError("The Skill UUID is not in the current catalog.")
        return self._record(
            "read_skill",
            {"skill_uuid": skill_uuid},
            {"uuid": skill_uuid, "version": skill["version"], "markdown": skill["markdown"]},
        )
