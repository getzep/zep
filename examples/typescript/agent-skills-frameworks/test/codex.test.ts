import { describe, expect, it } from "vitest";
import type { ThreadEvent } from "@openai/codex-sdk";
import {
  createCodexBankingMcpConfig,
  createCodexProviderConfig,
  mapCodexEvent,
} from "../agent/codex.js";

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

  it("fails the episode when a command, file, or web tool runs", () => {
    const forbiddenEvents: [ThreadEvent, string][] = [
      [{
        type: "item.completed",
        item: { type: "command_execution", id: "cmd", command: "echo unsafe", aggregated_output: "", status: "completed" },
      } as ThreadEvent, "command_execution"],
      [{
        type: "item.completed",
        item: { type: "file_change", id: "file", changes: [{ path: "agent.ts", kind: "update" }], status: "completed" },
      } as ThreadEvent, "file_change"],
      [{
        type: "item.completed",
        item: { type: "web_search", id: "search", query: "banking policies" },
      } as ThreadEvent, "web_search"],
    ];

    for (const [event, type] of forbiddenEvents) {
      expect(mapCodexEvent(event)).toEqual({
        kind: "failure",
        message: `Codex emitted forbidden ${type} activity.`,
      });
    }
  });

  it("ignores Codex MCP resource-discovery events", () => {
    for (const tool of ["list_mcp_resources", "list_mcp_resource_templates"]) {
      expect(mapCodexEvent({
        type: "item.started",
        item: { type: "mcp_tool_call", id: `resource-${tool}`, server: "banking", tool: `mcp__banking__${tool}`, arguments: {}, status: "in_progress" },
      } as ThreadEvent)).toBeNull();
    }
  });
});

describe("Codex provider configuration", () => {
  it("selects the HTTP Responses provider", () => {
    expect(createCodexProviderConfig("http://127.0.0.1:14100/openai/v1")).toEqual({
      model_provider: "openai_http",
      model_providers: {
        openai_http: {
          name: "OpenAI",
          base_url: "http://127.0.0.1:14100/openai/v1",
          env_key: "OPENAI_API_KEY",
          wire_api: "responses",
          supports_websockets: false,
        },
      },
    });
  });

  it("pre-approves only the nine banking MCP tools", () => {
    expect(createCodexBankingMcpConfig()).toEqual({
      default_tools_approval_mode: "approve",
      enabled_tools: [
        "get_case",
        "list_documents",
        "read_document",
        "calculator",
        "quote_interest",
        "compare_packages",
        "credit_interest",
        "file_discrepancy_report",
        "read_skill",
      ],
    });
  });
});
