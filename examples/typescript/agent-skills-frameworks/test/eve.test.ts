import { describe, expect, it } from "vitest";
import { mapEveEvent } from "../agent/hooks/trace.js";

describe("Eve event mapping", () => {
  it("maps tool requests, tool results, and assistant text to Trace events", () => {
    const request = mapEveEvent({
      type: "call.requested",
      data: { callId: "call-1", capability: { kind: "tool", name: "banking__compare_packages" }, input: { packages: [] } },
    });
    expect(request).toEqual({
      eventType: "tool_call",
      content: "{\"packages\":[]}",
      toolName: "compare_packages",
      toolCallId: "call-1",
    });
    expect(mapEveEvent({
      type: "call.settled",
      data: { callId: "call-1", outcome: "failed", error: { message: "bad input" } },
    }, "compare_packages")).toMatchObject({ eventType: "tool_result", content: "bad input" });
    expect(mapEveEvent({
      type: "content.completed",
      data: { kind: "text", value: "complete" },
    })).toEqual({ eventType: "decision", content: "complete" });
  });
});
