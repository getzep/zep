import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";
import { makeCase } from "../src/banking.js";
import { BankingTools } from "../src/tools.js";
import { createBankingMcpServer } from "../agent/codex-mcp-server.js";

describe("Codex banking MCP server", () => {
  it("lists nine tools and validates a package comparison call", async () => {
    const tools = new BankingTools(makeCase(22));
    const server = createBankingMcpServer(tools, () => {});
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const client = new Client({ name: "test", version: "1.0.0" });
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    try {
      const listed = await client.listTools();
      expect(listed.tools).toHaveLength(9);
      const packages = tools.caseData.savings.map((account) => ({
        account_id: account.account_id, checking_account_id: null, card_id: null,
      }));
      const result = await client.callTool({ name: "compare_packages", arguments: { packages } });
      expect(result.isError).not.toBe(true);
      expect(tools.calls[0].arguments.packages).toEqual(packages);
      const invalid = await client.callTool({ name: "read_document", arguments: { document_id: "invalid" } });
      expect(invalid.isError).toBe(true);
    } finally {
      await client.close();
      await server.close();
    }
  });
});
