import { appendFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { makeCase, expectedDatabase } from "../src/banking.js";
import { BankingTools } from "../src/tools.js";
import { createLedgerFetch, normalizeBaseUrl, TraceRecorder, type ZepSkills } from "../src/zep-skills.js";
import { FRAMEWORKS, runStage } from "../src/run.js";

describe("Zep Skills client", () => {
  it("normalizes HTTPS URLs and records mocked fetch results", async () => {
    expect(normalizeBaseUrl("https://api.example.test/root/")).toBe("https://api.example.test/root/api/v4");
    expect(() => normalizeBaseUrl("http://api.example.test")).toThrow("HTTPS");
    const dir = mkdtempSync(join(tmpdir(), "zep-ledger-"));
    const ledger = join(dir, "requests.jsonl");
    const fetchMock = vi.fn(async () => new Response(null, { status: 202 }));
    await createLedgerFetch(ledger, fetchMock)("https://api.example.test/api/v4/agents", { method: "POST" });
    expect(JSON.parse(readFileSync(ledger, "utf8"))).toEqual({ method: "POST", path: "/api/v4/agents", status: 202 });
    rmSync(dir, { recursive: true, force: true });
  });

  it("sends one close request when polling fails after acceptance", async () => {
    const trace = { close: vi.fn(async () => ({ task: { uuid: "task-1" } })) };
    const client = {
      agent: {
        trace: {
          create: vi.fn(async () => ({ uuid: "trace-1" })),
          appendEvent: vi.fn(async () => ({})),
          close: trace.close,
          get: vi.fn(async () => ({})),
        },
      },
      task: { get: vi.fn(async () => { throw new Error("poll failed"); }) },
    };
    const zep = { framework: "mastra", client } as unknown as ZepSkills;
    const oldAdapter = FRAMEWORKS.mastra;
    FRAMEWORKS.mastra = async () => ({
      runEpisode: async (caseData) => ({
        finalAnswer: "Done.",
        tokens: 1,
        tools: Object.assign(new BankingTools(caseData), { database: expectedDatabase(caseData) }),
      }),
    });
    const dir = mkdtempSync(join(tmpdir(), "zep-close-"));
    try {
      await expect(runStage(join(dir, "teacher"), zep, "agent-1", "mastra", makeCase(22), "model", "none", [], false)).rejects.toThrow("poll failed");
      expect(trace.close).toHaveBeenCalledTimes(1);
    } finally {
      FRAMEWORKS.mastra = oldAdapter;
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
