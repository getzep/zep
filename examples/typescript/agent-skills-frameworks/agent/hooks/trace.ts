import { defineHook } from "eve/hooks";
import { TraceRecorder, ZepSkills } from "../../src/zep-skills.js";
import { writeHookEventCount } from "../eve-event-count.js";

let recorder: TraceRecorder | undefined;
const names = new Map<string, string>();

export function mapEveEvent(
  event: { type: string; data: Record<string, any> },
  toolName?: string,
) {
  if (event.type === "call.requested" && event.data.capability?.kind === "tool") {
    const name = String(event.data.capability.name).replace(/^.*__/, "");
    const allowed = ["get_case", "list_documents", "read_document", "calculator", "quote_interest", "compare_packages", "credit_interest", "file_discrepancy_report", "read_skill"];
    if (!allowed.includes(name)) return null;
    return { eventType: "tool_call", content: JSON.stringify(event.data.input ?? {}), toolName: name, toolCallId: String(event.data.callId) };
  }
  if (event.type === "call.settled" && toolName) {
    const result = event.data.outcome === "completed" ? event.data.output : event.data.error?.message ?? "Tool failed.";
    return { eventType: "tool_result", content: typeof result === "string" ? result : JSON.stringify(result), toolName, toolCallId: String(event.data.callId) };
  }
  if (event.type === "content.completed" && event.data.kind === "text" && typeof event.data.value === "string") {
    return { eventType: "decision", content: event.data.value };
  }
  return null;
}

function getRecorder() {
  if (recorder) return recorder;
  const agent = process.env.BANKING_AGENT_UUID;
  const trace = process.env.BANKING_TRACE_UUID;
  const ledger = process.env.BANKING_LEDGER_PATH;
  if (!agent || !trace || !ledger || !process.env.BANKING_EVENT_COUNT_PATH) {
    throw new Error("The Eve Trace hook is missing its run identifiers.");
  }
  const zep = new ZepSkills(ledger, "eve");
  recorder = new TraceRecorder(zep, agent);
  recorder.traceUuid = trace;
  return recorder;
}

async function recordEvent(event: NonNullable<ReturnType<typeof mapEveEvent>>) {
  const eventCountPath = process.env.BANKING_EVENT_COUNT_PATH;
  if (!eventCountPath) throw new Error("The Eve Trace hook is missing its event count path.");
  const activeRecorder = getRecorder();
  await activeRecorder.append(event.eventType, event.content, event.toolName, event.toolCallId);
  writeHookEventCount(eventCountPath, activeRecorder.eventCount);
}

export default defineHook({
  events: {
    async "call.requested"(event) {
      const mapped = mapEveEvent(event);
      if (!mapped || mapped.eventType !== "tool_call") return;
      if (!mapped.toolName) return;
      names.set(mapped.toolCallId, mapped.toolName);
      await recordEvent(mapped);
    },
    async "call.settled"(event) {
      const name = names.get(event.data.callId);
      if (!name) return;
      const mapped = mapEveEvent(event, name);
      if (!mapped) return;
      await recordEvent(mapped);
      names.delete(event.data.callId);
    },
    async "content.completed"(event) {
      const mapped = mapEveEvent(event);
      if (mapped) await recordEvent(mapped);
    },
  },
});
