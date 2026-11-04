import { MockLanguageModelV4 } from "ai/test";
import { describe, expect, it } from "vitest";
import { makeCase } from "../src/banking.js";
import { BankingTools } from "../src/tools.js";
import { runEpisode } from "../agent/mastra.js";

function mockModel(toolName: string, args: unknown, textBeforeTool = "") {
  const usage = {
    inputTokens: { total: 2, noCache: 2, cacheRead: undefined, cacheWrite: undefined },
    outputTokens: { total: 3, text: 3, reasoning: undefined },
  };
  return new MockLanguageModelV4({
    doGenerate: [
      {
        content: [
          ...(textBeforeTool ? [{ type: "text" as const, text: textBeforeTool }] : []),
          { type: "tool-call", toolCallId: "call-1", toolName, input: JSON.stringify(args) },
        ],
        finishReason: { unified: "tool-calls", raw: "tool_use" },
        usage,
        warnings: [],
      },
      {
        content: [{ type: "text", text: "Done." }],
        finishReason: { unified: "stop", raw: "end_turn" },
        usage,
        warnings: [],
      },
    ],
  });
}

describe("Mastra adapter", () => {
  it("records tool and assistant events from model callbacks", async () => {
    process.env.ANTHROPIC_API_KEY = "test-key";
    const events: { type: string; content: string; name?: string }[] = [];
    const recorder = { append: async (type: string, content: string, name?: string) => { events.push({ type, content, name }); } };
    const result = await runEpisode(makeCase(22), "claude-haiku-5-5", "none", [], recorder, false, {
      testModel: mockModel("read_document", { document_id: "invalid" }, "Checking the record."),
    });
    expect(result.finalAnswer).toBe("Checking the record.Done.");
    expect(events.map((event) => event.type)).toEqual(["input_reference", "tool_call", "tool_result", "decision", "decision"]);
    expect(events[1].name).toBe("read_document");
    expect(events[2].content).toContain("not in list_documents");
  });

  it("returns ToolError to the model and records it as a tool result", async () => {
    const events: { type: string; content: string; name?: string }[] = [];
    const model = mockModel("read_document", { document_id: "invalid" });
    await runEpisode(makeCase(22), "claude-haiku-5-5", "none", [], {
      append: async (type: string, content: string, name?: string) => { events.push({ type, content, name }); },
    }, false, { testModel: model });
    expect(events.find((event) => event.type === "tool_result")?.content).toContain("not in list_documents");
    expect(JSON.stringify(model.doGenerateCalls[1].prompt)).toContain("not in list_documents");
  });

  it("accepts package objects for compare_packages", async () => {
    const caseData = makeCase(22);
    const packages = caseData.savings.map((account) => ({
      account_id: account.account_id,
      checking_account_id: null,
      card_id: null,
    }));
    const events: { type: string; content: string; name?: string }[] = [];
    const result = await runEpisode(caseData, "claude-haiku-5-5", "none", [], {
      append: async (type: string, content: string, name?: string) => { events.push({ type, content, name }); },
    }, false, { testModel: mockModel("compare_packages", { packages }) });
    expect(result.tools).toBeInstanceOf(BankingTools);
    expect(result.tools.calls[0].arguments.packages).toEqual(packages);
    expect(events.find((event) => event.type === "tool_result")?.content).toContain("permitted");
  });
});
