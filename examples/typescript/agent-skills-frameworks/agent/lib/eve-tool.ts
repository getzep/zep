import { readFileSync, writeFileSync } from "node:fs";
import { defineTool } from "eve/tools";
import { BankingTools, invokeBankingTool, toolDescriptions, toolSchemas, type ToolName } from "../../src/tools.js";
import type { Case } from "../../src/banking.js";

const inputPath = process.env.BANKING_INPUT_PATH;
const statePath = process.env.BANKING_STATE_PATH;
if (!inputPath || !statePath) throw new Error("The Eve tools require input and state paths.");
const input = JSON.parse(readFileSync(inputPath, "utf8")) as {
  case: Case;
  skills: Record<string, unknown>[];
};
const banking = new BankingTools(input.case, input.skills);

function save() {
  writeFileSync(statePath!, JSON.stringify({ calls: banking.calls, database: banking.database }));
}

export function eveBankingTool(name: ToolName) {
  return defineTool({
    description: toolDescriptions[name],
    inputSchema: toolSchemas[name],
    async execute(args) {
      const result = invokeBankingTool(banking, name, args as Record<string, unknown>);
      save();
      return result;
    },
  });
}
