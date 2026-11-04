import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { makeCase, score } from "../src/banking.js";
import { BankingTools, ToolError, invokeBankingTool } from "../src/tools.js";

const golden = JSON.parse(readFileSync(new URL("./golden/banking.json", import.meta.url), "utf8"));

describe("banking tools", () => {
  it("reaches the expected database through the package comparison and correction tools", () => {
    const row = golden.cases.find((item: { seed: number }) => item.seed === 22);
    const caseData = makeCase(22);
    const tools = new BankingTools(caseData);
    expect(tools.get_case()).toEqual(caseData);
    expect(tools.list_documents()).toHaveLength(6);
    const comparison = invokeBankingTool(tools, "compare_packages", { packages: row.comparison_inputs }) as { permitted: boolean; quotes: { account_id: string }[] };
    expect(comparison.permitted).toBe(true);
    for (const account of caseData.savings) {
      const result = tools.credit_interest(account.account_id) as { saved: boolean; credit?: { credit_id: string } };
      if (result.saved && result.credit) tools.file_discrepancy_report(account.account_id, result.credit.credit_id);
    }
    expect(score(caseData, tools.database).passed).toBe(true);
  });

  it("turns invalid tool requests into ToolError values", () => {
    const tools = new BankingTools(makeCase(22));
    expect(() => tools.read_document("not-a-document")).toThrow(ToolError);
    expect(() => invokeBankingTool(tools, "compare_packages", { packages: "invalid" })).toThrow();
  });

  it("validates nested package inputs as selections before dispatch", () => {
    const row = golden.cases[0];
    const tools = new BankingTools(makeCase(row.seed));
    const result = invokeBankingTool(tools, "compare_packages", { packages: row.comparison_inputs }) as { permitted: boolean };
    expect(result.permitted).toBe(true);
    expect((tools.calls[0].arguments.packages as unknown[])[0]).toEqual(row.comparison_inputs[0]);
  });
});
