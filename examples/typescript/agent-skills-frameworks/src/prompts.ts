import type { Case } from "./banking.js";

export const SYSTEM = (
  "You are a banking service agent. Resolve the customer's request using the local " +
  "banking tools and the bank's policies. The customer is already verified and has " +
  "authorized the necessary correction credits and reports. Do not ask for more input. " +
  "Use the records and policy evidence instead of inventing account facts or rates. " +
  "Give a short final summary of the actions and calculations. " +
  "If a relevant Skill is in the catalog, read the Skill before acting. " +
  "The Skill catalog is: {catalog}"
);

export function skillCatalog(skills: Record<string, unknown>[]) {
  return skills.map((skill) => ({
    uuid: skill.uuid,
    name: skill.name ?? "",
    description: skill.description ?? "",
    version: skill.version,
  }));
}

export const USER = (
  "Please investigate the savings interest for statement month {month} on all my " +
  "savings accounts. Correct each underpayment and save the discrepancy reports. " +
  "You have my permission to complete these actions. My case ID is {case_id}."
);

export const TEACHING_REQUEST = (
  "For this teaching run, keep the action summary short. Then show a comparison table " +
  "of every eligible, permitted checking/card package for each savings account, " +
  "including its three capped bonus contributions. Submit all permitted packages to " +
  "compare_packages, including unboosted and single-source alternatives. This tool " +
  "compares complete allocations without reusing sources, but it does not generate " +
  "omitted packages. Complete the comparisons before posting any credit. The bank retains " +
  "the best quoted allocation. Post each correction using its savings account ID, then " +
  "file the report using that account ID and its saved credit ID. " +
  "Use quote_interest to evaluate each package instead of reimplementing its arithmetic. " +
  "After the tables, document the reusable procedure that another banking agent " +
  "can follow on different account records. State the general selection rule with " +
  "variables and policy caps. Do not use customer identifiers or case-specific " +
  "amounts in the reusable procedure. Use symbolic variables for source identities. " +
  "Do not use c1, c2, c3, k1, k2, or k3 as symbolic names. " +
  "The reusable procedure must not prescribe fixed product pairings; the winning " +
  "allocation depends on each case's balances."
);

export function userPrompt(caseData: Case, teaching: boolean) {
  const prompt = USER.replace("{month}", caseData.statement_month).replace("{case_id}", caseData.case_id);
  return teaching ? `${prompt}\n\n${TEACHING_REQUEST}` : prompt;
}
