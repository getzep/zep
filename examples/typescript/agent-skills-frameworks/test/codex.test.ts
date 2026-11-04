import { describe, expect, it } from "vitest";
import type { ThreadEvent } from "@openai/codex-sdk";
import { mapCodexEvent } from "../agent/codex.js";

describe("Codex event mapping", () => {
  it("records bare MCP tool names, errors, decisions, and token totals", () => {
    const started = mapCodexEvent({
      type: "item.started",
      item: { type: "mcp_tool_call", id: "call-1", server: "banking", tool: "mcp__banking__compare_packages", arguments: { packages: [] }, status: "in_progress" },
    } as ThreadEvent);
    expect(started).toEqual({
      kind: "trace",
      eventType: "tool_call",
      content: "{\"packages\":[]}",
      toolName: "compare_packages",
      toolCallId: "call-1",
    });
    const failed = mapCodexEvent({
      type: "item.completed",
      item: { type: "mcp_tool_call", id: "call-1", server: "banking", tool: "compare_packages", arguments: {}, error: { message: "invalid" }, status: "failed" },
    } as ThreadEvent);
    expect(failed).toMatchObject({ kind: "trace", eventType: "tool_result", content: "invalid" });
    expect(mapCodexEvent({
      type: "item.completed", item: { type: "agent_message", id: "m", text: "done" },
    } as ThreadEvent)).toEqual({ kind: "answer", answer: "done" });
    expect(mapCodexEvent({
      type: "turn.completed",
      usage: { input_tokens: 2, output_tokens: 3, cached_input_tokens: 4, cache_write_input_tokens: 5, reasoning_output_tokens: 0 },
    } as ThreadEvent)).toEqual({ kind: "tokens", tokens: 14 });
  });

  it("fails the episode when a built-in command tool runs", () => {
    expect(mapCodexEvent({
      type: "item.completed",
      item: { type: "command_execution", id: "cmd", command: "echo unsafe", aggregated_output: "", status: "completed" },
    } as ThreadEvent)).toMatchObject({ kind: "failure", message: expect.stringContaining("command_execution") });
  });
});
