import { randomUUID } from "node:crypto";
import { appendFileSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { ZepClient } from "@getzep/zep-cloud";

export const FAMILY = "banking.synthetic-monthly-correction";
export const QUERY = "Investigate savings interest, apply qualifying product boosts, credit shortfalls and save reports";
type TraceEventType = "input_reference" | "decision" | "tool_call" | "tool_result";

export function createLedgerFetch(ledger: string, baseFetch: typeof fetch = fetch): typeof fetch {
  return async (input, init) => {
    let status: number | null = null;
    try {
      const response = await baseFetch(input, init);
      status = response.status;
      return response;
    } finally {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url);
      appendFileSync(ledger, `${JSON.stringify({ method: init?.method ?? "GET", path: url.pathname, status })}\n`);
    }
  };
}

export function normalizeBaseUrl(value?: string) {
  if (!value) return undefined;
  const url = new URL(value);
  if (url.protocol !== "https:") throw new Error("ZEP_BASE_URL must use HTTPS.");
  url.pathname = `${url.pathname.replace(/\/+$/, "").replace(/\/api\/v4$/, "")}/api/v4`;
  return url.toString().replace(/\/$/, "");
}

export class ZepSkills {
  readonly client: ZepClient;

  constructor(readonly ledger: string, readonly framework: string) {
    const apiKey = process.env.ZEP_API_KEY;
    if (!apiKey) throw new Error("Set ZEP_API_KEY.");
    const baseUrl = normalizeBaseUrl(process.env.ZEP_BASE_URL);
    this.client = new ZepClient({ apiKey, baseUrl, maxRetries: 0, timeoutInSeconds: 120, fetch: createLedgerFetch(this.ledger) });
  }

  async createAgent() {
    return this.client.agent.create({
      agentId: `frameworks-banking-${this.framework}-${randomUUID()}`,
      name: "Synthetic monthly interest correction demo",
      securityDomain: "simple-banking-demo",
      memorySettings: {
        compilationMode: "on_close",
        approval: "auto",
        autoApprovalTaskFamilies: [FAMILY],
      },
    }, { idempotencyKey: randomUUID() });
  }

  async searchSkills(agent: string) {
    const page = await this.client.agent.skill.search(agent, {
      pageSize: 20,
      query: QUERY,
      taskFamily: FAMILY,
      includeMarkdown: true,
      markdownFormat: "agent",
    });
    const searchId = page.response.searchId;
    if (!searchId) throw new Error("Zep did not return a Skill search UUID.");
    const skills = (page.response.items ?? []).map((skill) => skill as unknown as Record<string, any>)
      .filter((skill) => skill.uuid && Number.isInteger(skill.version) && String(skill.markdown ?? "").trim());
    return { searchId, skills };
  }

  async waitForSkill(agent: string, seconds = 900) {
    const deadline = Date.now() + seconds * 1000;
    let last = "";
    let stable = 0;
    while (Date.now() < deadline) {
      const learning = await this.client.agent.learning.get(agent, { taskFamily: FAMILY });
      const runs = await this.client.agent.learning.listRuns(agent, { taskFamily: FAMILY });
      const latest = runs.response.items?.[0];
      if (latest && learning.compilationPending === false) {
        if (latest.decision !== "produced") {
          throw new Error(`The compiler did not produce a Skill: ${latest.decision}; reason: ${latest.rejectionClass ?? latest.reasonText ?? "unknown"}.`);
        }
        const { skills } = await this.searchSkills(agent);
        const pin = JSON.stringify(skills.map((skill) => [skill.uuid, skill.version, skill.markdown]).sort());
        stable = pin === last ? stable + 1 : 1;
        last = pin;
        if (stable >= 3 && skills.length) return { learning, runs, skills };
      }
      await delay(5000);
    }
    throw new Error("No complete admitted Skill is searchable.");
  }

  async recordSkillUse(agent: string, skillUuid: string, searchId: string, traceUuid: string, used: boolean, passed: boolean) {
    const use = await this.client.agent.skill.use.create(agent, skillUuid, {
      searchId, traceUuid, usage: used ? "used" : "ignored",
    }, { idempotencyKey: randomUUID() });
    if (!use.uuid) throw new Error("Zep did not return a Skill use UUID.");
    const outcome = await this.client.agent.skill.use.addOutcome(agent, skillUuid, use.uuid, {
      outcome: passed ? "succeeded" : "failed",
    }, { idempotencyKey: randomUUID() });
    return { uuid: use.uuid, outcome };
  }
}

export class TraceRecorder {
  traceUuid: string | null = null;
  eventCount = 0;
  closeRequested = false;

  constructor(readonly zep: ZepSkills, readonly agent: string) {}

  async start() {
    if (this.traceUuid) throw new Error("This Trace recorder has already started.");
    const trace = await this.zep.client.agent.trace.create(this.agent, {
      objective: QUERY,
      taskFamily: FAMILY,
      metadata: { source: "agent-skills-frameworks", framework: this.zep.framework, data: "invented" },
    }, { idempotencyKey: randomUUID() });
    if (!trace.uuid) throw new Error("Zep did not return a Trace UUID.");
    this.traceUuid = trace.uuid;
    return trace.uuid;
  }

  async append(eventType: string, content: string, toolName?: string, toolCallId?: string) {
    if (!this.traceUuid) throw new Error("Start the Trace before appending events.");
    await this.zep.client.agent.trace.appendEvent(this.agent, this.traceUuid, {
      eventType: eventType as TraceEventType,
      content,
      toolCallId,
      context: toolName ? { tools: [{ name: toolName }] } : undefined,
    }, { idempotencyKey: randomUUID() });
    this.eventCount += 1;
  }

  async close(passed: boolean) {
    if (!this.traceUuid) throw new Error("Start the Trace before closing it.");
    const outcome = passed ? "succeeded" : "failed";
    const close = await this.zep.client.agent.trace.close(this.agent, this.traceUuid, {
      outcome,
      verification: "external",
      verifier: {
        verifierId: "frameworks-banking-state",
        class: "harness",
        outcome,
        evidenceReference: this.traceUuid,
      },
    }, { idempotencyKey: randomUUID() });
    const taskUuid = close.task?.uuid;
    if (!taskUuid) throw new Error("Zep did not return a Trace close Task UUID.");
    this.closeRequested = true;
    const deadline = Date.now() + 900_000;
    let task: any;
    while (Date.now() < deadline) {
      task = await this.zep.client.task.get(taskUuid);
      if (task.status === "succeeded") break;
      if (!["pending", "processing"].includes(task.status)) throw new Error(`Trace close Task ended in ${task.status}.`);
      await delay(5000);
    }
    if (task?.status !== "succeeded") throw new Error("The Trace close Task is still running.");
    const finalTrace = await this.zep.client.agent.trace.get(this.agent, this.traceUuid);
    if (finalTrace.status !== "closed" || finalTrace.verification !== "external") {
      throw new Error("Zep did not close the externally verified Trace.");
    }
    return { trace_uuid: this.traceUuid, close, task, final_trace: finalTrace, event_count: this.eventCount };
  }
}
