import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { BankingTools, ToolError, toolDescriptions, toolSchemas, invokeBankingTool, type ToolName } from "../src/tools.js";
import type { Case } from "../src/banking.js";

export function createBankingMcpServer(tools: BankingTools, saveState: () => void) {
  const server = new McpServer({ name: "banking", version: "1.0.0" });
  const registerTool = server.registerTool.bind(server) as (...args: any[]) => void;
  for (const name of Object.keys(toolSchemas) as ToolName[]) {
    registerTool(name, {
      description: toolDescriptions[name],
      inputSchema: toolSchemas[name].shape,
    }, async (args: Record<string, unknown>) => {
      try {
        const output = invokeBankingTool(tools, name, args as Record<string, unknown>);
        saveState();
        return { content: [{ type: "text" as const, text: JSON.stringify(output) }], structuredContent: output as Record<string, unknown> };
      } catch (error) {
        const message = error instanceof ToolError ? error.message : String(error);
        return { content: [{ type: "text" as const, text: message }], isError: true };
      }
    });
  }
  return server;
}

async function main() {
  const inputPath = process.env.BANKING_INPUT_PATH;
  const statePath = process.env.BANKING_STATE_PATH;
  if (!inputPath || !statePath) throw new Error("The banking MCP server requires input and state paths.");
  const input = JSON.parse(readFileSync(inputPath, "utf8")) as {
    case: Case;
    skills: Record<string, unknown>[];
  };
  const tools = new BankingTools(input.case, input.skills);
  const saveState = () => writeFileSync(statePath, JSON.stringify({ calls: tools.calls, database: tools.database }));
  saveState();
  await createBankingMcpServer(tools, saveState).connect(new StdioServerTransport());
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
