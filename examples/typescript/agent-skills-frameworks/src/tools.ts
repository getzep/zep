import { Decimal } from "decimal.js";
import { z } from "zod";
import {
  calculate,
  compareRank,
  comparePackages,
  quoteInterest,
  type Allocation,
  type Case,
  type Database,
  type Selection,
} from "./banking.js";

function allocationRank(allocation: Allocation): Decimal[] {
  return [
    new Decimal(allocation.total_annual_bonus),
    ...allocation.quotes.flatMap((quote) => [
      new Decimal(quote.card_bonus_percent),
      new Decimal(quote.checking_bonus_percent),
    ]),
  ];
}

export class ToolError extends Error {}

export const selectionSchema = z.object({
  account_id: z.string(),
  checking_account_id: z.string().nullable().default(null),
  card_id: z.string().nullable().default(null),
});

export const toolSchemas = {
  get_case: z.object({}),
  list_documents: z.object({}),
  read_document: z.object({ document_id: z.string() }),
  calculator: z.object({ expression: z.string() }),
  quote_interest: selectionSchema,
  compare_packages: z.object({ packages: z.array(selectionSchema) }),
  credit_interest: z.object({ account_id: z.string() }),
  file_discrepancy_report: z.object({ account_id: z.string(), correction_credit_id: z.string() }),
  read_skill: z.object({ skill_uuid: z.string() }),
};
export type ToolName = keyof typeof toolSchemas;

export const toolDescriptions: Record<keyof typeof toolSchemas, string> = {
  get_case: "Get the synthetic banking case and its statement-month records.",
  list_documents: "List the bank policy documents available to this agent.",
  read_document: "Read a policy document returned by list_documents.",
  calculator: "Evaluate a basic arithmetic expression.",
  quote_interest: "Quote interest for one permitted savings account package.",
  compare_packages: "Compare complete product allocations across savings accounts.",
  credit_interest: "Post the selected correction for one savings account.",
  file_discrepancy_report: "Save a report for a posted interest correction.",
  read_skill: "Read one Skill in the current Skill catalog.",
};

export function invokeBankingTool(
  tools: BankingTools,
  name: keyof typeof toolSchemas,
  args: Record<string, unknown>,
): unknown {
  const input = toolSchemas[name].parse(args) as Record<string, unknown>;
  switch (name) {
    case "get_case": return tools.get_case();
    case "list_documents": return tools.list_documents();
    case "read_document": return tools.read_document(String(input.document_id));
    case "calculator": return tools.calculator(String(input.expression));
    case "quote_interest": return tools.quote_interest(
      String(input.account_id),
      input.checking_account_id as string | null,
      input.card_id as string | null,
    );
    case "compare_packages": return tools.compare_packages(input.packages as Selection[]);
    case "credit_interest": return tools.credit_interest(String(input.account_id));
    case "file_discrepancy_report": return tools.file_discrepancy_report(
      String(input.account_id),
      String(input.correction_credit_id),
    );
    case "read_skill": return tools.read_skill(String(input.skill_uuid));
  }
}

export const DOCUMENTS: Record<string, [string, string]> = {
  "monthly-interest": ["Monthly interest and corrections", "This training bank uses a nominal annual rate, not an effective yield. For a full month with a constant balance, the base rate applies to the full statement balance. The checking bonus applies only to the first $5,000. The card bonus applies only to the first $10,000. A package bonus applies only to the first $7,500 and only when an eligible checking account and card are paired. See the separate package bonus table. Expected interest = (balance times base rate percent plus min(balance, 5000) times checking bonus percent plus min(balance, 10000) times card bonus percent plus min(balance, 7500) times package bonus percent) / 100 / 12. Round once, half-up to cents. Do not round the four components separately. Use the statement balance before correction credits. A package has at most one checking boost and one card boost. Use the separate product-specific tables. Current account status is not enough: all bonus qualification tests must be met during the statement month. Subtract net interest already posted for the statement month, including reversals and earlier interest corrections. Post one credit for the remaining shortfall. A credit with a different statement month is not part of the calculation."],
  "checking-boosts": ["Checking boosts for Cedar, Orchid, Maple, and Willow savings", "Checking accounts qualify only if they are active, payroll deposits are at least $1500 for the statement month, and there are at least 10 debit purchases that month. For Cedar: Everyday gives +0.30 percentage points; Plus gives +0.65; Premier gives +1.10. For Orchid: Everyday gives +0.55; Plus gives +0.60; Premier gives +0.20. For Maple: Everyday gives +0.75; Plus gives +0.15; Premier gives +0.95. For Willow: Everyday gives +0.40; Plus gives +0.55; Premier gives +1.00. Select checking and card boosts as a permitted package. Do not sum the boosts. The best checking account can differ by savings product."],
  "card-boosts": ["Card boosts for Cedar, Orchid, Maple, and Willow savings", "Cards qualify only if they are open, are not delinquent, and statement-month qualifying purchases are at least $500. For Cedar: Rewards gives +0.35 percentage points; Platinum gives +0.60; Travel gives +0.90. For Orchid: Rewards gives +0.75; Platinum gives +0.25; Travel gives +0.40. For Maple: Rewards gives +0.65; Platinum gives +0.80; Travel gives +1.10. For Willow: Rewards gives +0.90; Platinum gives +0.65; Travel gives +1.00. Use only an eligible card boost in a permitted relationship package. Do not sum the boosts. Do not use a card's advertised boost without qualification."],
  "package-boosts": ["Extra bonuses for complete checking and card packages", "Only a package with both an eligible checking account and an eligible card receives this extra nominal annual bonus on the first $7,500. Use its exact savings product, checking product, and card product. The bonus is in percentage points and is added to the base, checking, and card terms before monthly rounding. For each product, columns are Rewards, Platinum, Travel. Cedar: Everyday +0.50, +0.10, +0.35; Plus +0.35, +0.45, +0.10; Premier +0.25, +0.50, excluded. Orchid: Everyday excluded, +0.10, +0.40; Plus +0.05, +0.60, +0.30; Premier +0.50, +0.35, +0.55. Maple: Everyday +0.30, +0.50, excluded; Plus +0.60, +0.05, +0.40; Premier +0, +0, +0. Willow: Everyday +0.10, +0.30, +0.20; Plus +0.35, +0.15, +0.25; Premier +0, excluded, +0. A package that uses only checking or only a card has no package bonus. In every quote and report, checking boosted balance is min(balance, 5000), card boosted balance is min(balance, 10000), and package boosted balance is min(balance, 7500), even when a category is unused or its rate is zero."],
  "relationship-packages": ["Relationship package eligibility and automatic pricing", "The bank applies the allocation that pays the most total interest across all savings accounts. A checking account or card can support only one savings account during the statement month. No customer enrollment is needed. Compare min(balance, 5000) times checking bonus percent plus min(balance, 10000) times card bonus percent plus min(balance, 7500) times the applicable package bonus percent. Do not select a package by the sum of its advertised percentages. Compare the total unrounded annual bonus dollars across all savings accounts. If allocations tie, order savings by account ID and prefer the higher card bonus for the first account, then its higher checking bonus, then repeat for the next account. A package may use one eligible checking boost, one eligible card boost, or both. A package may leave either category unused. The following exclusions apply: Cedar cannot combine a Premier checking boost with a Travel card boost. Orchid cannot combine an Everyday checking boost with a Rewards card boost. Maple cannot combine an Everyday checking boost with a Travel card boost. Willow cannot combine a Premier checking boost with a Platinum card boost. Each excluded combination may still supply one of its two boosts on its own. All other checking and card combinations are permitted. An individual quote prices only one proposed package; it does not select an allocation or reserve a checking account or card. The allocation rule overrides any direction to maximize accounts separately."],
  "rates-and-reports": ["Base rates and discrepancy reports", "Cedar has a 2.15 percent nominal annual base rate. Orchid has a 4.85 percent nominal annual base rate. Maple has a 3.25 percent nominal annual base rate. Willow has a 3.90 percent nominal annual base rate. After posting a correction credit, save one discrepancy report per affected savings account. The report contains the statement month, the base rate percent, checking bonus percent, card bonus percent, package bonus percent, and package boosted balance, the selected checking account ID and card ID (null for an unused category), checking boosted balance, card boosted balance, expected interest, net interest already posted before this run, and the correction credit ID. Use reason_code interest_shortfall for correction credits. Do not change an unrelated account or month."],
};

export class BankingTools {
  readonly database: Database = { credits: [], reports: [] };
  readonly calls: { name: string; arguments: Record<string, unknown>; result: unknown }[] = [];
  private best: ReturnType<typeof comparePackages> | null = null;
  private locked = false;

  constructor(readonly caseData: Case, readonly skills: Record<string, unknown>[] = []) {}

  private record(name: string, args: Record<string, unknown>, result: unknown) {
    this.calls.push({ name, arguments: args, result });
    return result;
  }
  get_case() { return this.record("get_case", {}, this.caseData); }
  list_documents() {
    return this.record("list_documents", {}, Object.entries(DOCUMENTS).map(([id, [title]]) => ({ id, title })));
  }
  read_document(document_id: string) {
    const item = DOCUMENTS[document_id];
    if (!item) throw new ToolError("The document ID is not in list_documents.");
    return this.record("read_document", { document_id }, { title: item[0], content: item[1] });
  }
  calculator(expression: string) {
    try { return this.record("calculator", { expression }, calculate(expression)); }
    catch (error) { throw new ToolError(error instanceof Error ? error.message : String(error)); }
  }
  quote_interest(account_id: string, checking_account_id: string | null = null, card_id: string | null = null) {
    const args = { account_id, checking_account_id, card_id };
    return this.record("quote_interest", args, quoteInterest(this.caseData, args));
  }
  compare_packages(packages: Selection[]) {
    const args = { packages: packages.map((item) => ({ ...item })) };
    if (this.locked) return this.record("compare_packages", args, { permitted: false, reason: "The first saved credit has locked the allocation." });
    const result = comparePackages(this.caseData, packages);
    if (!result.permitted) return this.record("compare_packages", args, result);
    if (!this.best?.permitted || compareRank(allocationRank(result), allocationRank(this.best)) > 0) this.best = result;
    return this.record("compare_packages", args, this.best);
  }
  private selected(account_id: string): Selection | null {
    if (!this.best?.permitted) return null;
    const quote = this.best.quotes.find((item) => item.account_id === account_id);
    return quote ? { account_id, checking_account_id: quote.checking_account_id, card_id: quote.card_id } : null;
  }
  credit_interest(account_id: string) {
    const args = { account_id };
    const selection = this.selected(account_id);
    if (!selection) return this.record("credit_interest", args, { saved: false, reason: "Compare packages before posting a correction." });
    const quote = quoteInterest(this.caseData, selection);
    if (!quote.permitted || Number(quote.correction_amount) <= 0) return this.record("credit_interest", args, { saved: false, reason: quote.permitted ? "The selected package has no interest shortfall." : quote.reason });
    const credit_id = `credit-${account_id}-${this.caseData.statement_month}`;
    if (this.database.credits.some((item) => item.credit_id === credit_id)) throw new ToolError("A correction for this account and month already exists.");
    const credit = { credit_id, account_id, statement_month: this.caseData.statement_month, amount: quote.correction_amount, reason_code: "interest_shortfall" };
    this.database.credits.push(credit);
    this.locked = true;
    return this.record("credit_interest", args, { saved: true, credit, package: selection });
  }
  file_discrepancy_report(account_id: string, correction_credit_id: string) {
    const args = { account_id, correction_credit_id };
    const selection = this.selected(account_id);
    const credit = this.database.credits.find((item) => item.credit_id === correction_credit_id);
    if (!selection || !credit || credit.account_id !== account_id) return this.record("file_discrepancy_report", args, { saved: false, reason: "The report must refer to a saved correction credit." });
    if (this.database.reports.some((item) => item.account_id === account_id && item.statement_month === this.caseData.statement_month)) throw new ToolError("A report already exists for this account and month.");
    const quote = quoteInterest(this.caseData, selection);
    if (!quote.permitted) return this.record("file_discrepancy_report", args, { saved: false, reason: quote.reason });
    const { permitted: _p, annual_bonus: _a, correction_amount: _c, ...report } = quote;
    const saved = { ...report, correction_credit_id };
    this.database.reports.push(saved);
    return this.record("file_discrepancy_report", args, { saved: true, report: saved });
  }
  read_skill(skill_uuid: string) {
    const skill = this.skills.find((item) => item.uuid === skill_uuid);
    if (!skill) throw new ToolError("The Skill UUID is not in the current catalog.");
    return this.record("read_skill", { skill_uuid }, { uuid: skill_uuid, version: skill.version, markdown: skill.markdown });
  }
}
