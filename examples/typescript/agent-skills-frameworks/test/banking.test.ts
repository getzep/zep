import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  comparePackages,
  expectedDatabase,
  makeCase,
  quoteInterest,
  score,
} from "../src/banking.js";
import { SYSTEM, TEACHING_REQUEST, USER } from "../src/prompts.js";

const golden = JSON.parse(readFileSync(new URL("./golden/banking.json", import.meta.url), "utf8"));

describe("Python banking parity", () => {
  it("matches all golden cases, scoring, quotes, and package comparisons", () => {
    expect(golden.prompts).toEqual({ SYSTEM, USER, TEACHING_REQUEST });
    for (const row of golden.cases) {
      const caseData = makeCase(row.seed);
      const bestInputs = row.comparison_inputs;
      const first = caseData.savings[0];
      expect({
        case: caseData,
        expected_database: expectedDatabase(caseData),
        score: score(caseData, expectedDatabase(caseData)),
        quotes: {
          best: quoteInterest(caseData, bestInputs[0]),
          unboosted: quoteInterest(caseData, { account_id: first.account_id, checking_account_id: null, card_id: null }),
          single_source: quoteInterest(caseData, { account_id: first.account_id, checking_account_id: caseData.checking[0].account_id, card_id: null }),
          refusal: quoteInterest(caseData, { account_id: first.account_id, checking_account_id: "not-a-checking-account", card_id: null }),
        },
        comparison: comparePackages(caseData, bestInputs),
        comparison_refusal: comparePackages(caseData, [...bestInputs.slice(0, caseData.savings.length), { account_id: first.account_id, checking_account_id: "not-a-checking-account", card_id: null }]),
      }).toEqual({
        case: row.case,
        expected_database: row.expected_database,
        score: row.score,
        quotes: row.quotes,
        comparison: row.comparison,
        comparison_refusal: row.comparison_refusal,
      });
    }
  });
});
