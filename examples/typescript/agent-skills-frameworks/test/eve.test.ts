import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { addHookEventCount, writeHookEventCount } from "../agent/eve-event-count.js";
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

  it("adds hook event counts from the episode file to the recorder", () => {
    const workdir = mkdtempSync(join(tmpdir(), "eve-event-count-"));
    const path = join(workdir, "trace-event-count.txt");
    const recorder = { eventCount: 1 };
    try {
      writeHookEventCount(path, 4);
      addHookEventCount(recorder, path);
      expect(readFileSync(path, "utf8")).toBe("4\n");
      expect(recorder.eventCount).toBe(5);
    } finally {
      rmSync(workdir, { recursive: true, force: true });
    }
  });
});
