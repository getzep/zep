import { once } from "node:events";
import { createServer } from "node:net";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { Client } from "eve/client";
import type { Case } from "../src/banking.js";
import { BankingTools } from "../src/tools.js";
import type { EpisodeResult } from "../src/episode.js";
import type { TraceRecorder } from "../src/zep-skills.js";
import { SYSTEM, userPrompt } from "../src/prompts.js";
import { addHookEventCount } from "./eve-event-count.js";

type EveRecorder = Pick<TraceRecorder, "append" | "eventCount" | "traceUuid">;

async function freePort() {
  const server = createServer();
  await new Promise<void>((resolveListen, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Could not allocate a local Eve port.");
  const port = address.port;
  await new Promise<void>((resolveClose, reject) => server.close((error) => error ? reject(error) : resolveClose()));
  return port;
}

async function waitForServer(client: Client, child: ReturnType<typeof spawn>) {
  const deadline = Date.now() + 60_000;
  let lastError: unknown;
  while (Date.now() < deadline && child.exitCode === null) {
    try {
      await client.health();
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    }
  }
  throw new Error(`Eve did not become ready: ${String(lastError)}`);
}

export async function runEpisode(
  caseData: Case,
  model: string,
  effort: string,
  skills: Record<string, unknown>[],
  recorder: EveRecorder,
  teaching: boolean,
): Promise<EpisodeResult> {
  const packageDir = process.cwd();
  const workdir = mkdtempSync(join(tmpdir(), "zep-banking-eve-"));
  const output = join(workdir, "state.json");
  const eventCountPath = join(workdir, "trace-event-count.txt");
  const input = join(workdir, "input.json");
  const traceUuid = recorder.traceUuid;
  const agentUuid = process.env.BANKING_AGENT_UUID;
  const ledger = process.env.BANKING_LEDGER_PATH;
  if (!traceUuid || !agentUuid || !ledger) throw new Error("The Eve adapter needs an open Trace.");
  let child: ReturnType<typeof spawn> | undefined;

  try {
    cpSync(join(packageDir, "agent"), join(workdir, "agent"), { recursive: true });
    cpSync(join(packageDir, "src"), join(workdir, "src"), { recursive: true });
    writeFileSync(join(workdir, "package.json"), readFileSync(join(packageDir, "package.json")));
    symlinkSync(join(packageDir, "node_modules"), join(workdir, "node_modules"), "dir");
    writeFileSync(join(workdir, "tsconfig.json"), readFileSync(join(packageDir, "tsconfig.json")));
    writeFileSync(join(workdir, "agent", "instructions.md"), SYSTEM.replace("{catalog}", JSON.stringify(skills)));
    writeFileSync(input, JSON.stringify({ case: caseData, skills }));
    writeFileSync(output, JSON.stringify({ calls: [], database: { credits: [], reports: [] } }));
    writeFileSync(eventCountPath, "0\n");

    const port = await freePort();
    const packageJson = JSON.parse(readFileSync(join(packageDir, "node_modules/eve/package.json"), "utf8")) as { bin: string | Record<string, string> };
    const eveBin = typeof packageJson.bin === "string" ? packageJson.bin : packageJson.bin.eve;
    child = spawn(process.execPath, [resolve(packageDir, "node_modules/eve", eveBin), "dev", "--no-ui", "--host", "127.0.0.1", "--port", String(port)], {
      cwd: workdir,
      env: {
        ...process.env as Record<string, string>,
        BANKING_MODEL: model,
        BANKING_EFFORT: effort,
        BANKING_INPUT_PATH: input,
        BANKING_STATE_PATH: output,
        BANKING_EVENT_COUNT_PATH: eventCountPath,
        BANKING_AGENT_UUID: agentUuid,
        BANKING_TRACE_UUID: traceUuid,
        BANKING_LEDGER_PATH: ledger,
      },
      stdio: ["ignore", "ignore", "pipe"],
    });
    child.stderr?.on("data", (data: Buffer) => process.stderr.write(data));
    const client = new Client({ baseUrl: `http://127.0.0.1:${port}` });
    await waitForServer(client, child);
    const prompt = userPrompt(caseData, teaching);
    await recorder.append("input_reference", prompt);
    // Eve's local server owns the session and tool state; its hooks write live Trace events directly through the typed Zep client.
    const { delivery } = await client.sessions.create({ message: prompt });
    let tokens = 0;
    const stream = (async () => {
      for await (const event of delivery.stream()) {
        if (event.type !== "usage.recorded") continue;
        const usage = event.data.usage;
        tokens += usage.inputTokens + usage.outputTokens + usage.cacheReadTokens + usage.cacheWriteTokens;
      }
    })();
    const result = await delivery.result();
    await stream;
    if (result.status !== "completed") throw new Error(`Eve delivery ended with ${result.status}.`);
    const state = JSON.parse(readFileSync(output, "utf8")) as {
      calls: BankingTools["calls"];
      database: BankingTools["database"];
    };
    const tools = new BankingTools(caseData, skills);
    tools.calls.push(...state.calls);
    tools.database.credits.push(...state.database.credits);
    tools.database.reports.push(...state.database.reports);
    addHookEventCount(recorder, eventCountPath);
    return { finalAnswer: result.reply ?? "", tokens, tools };
  } finally {
    if (child && child.exitCode === null) {
      child.kill("SIGTERM");
      await once(child, "exit");
    }
    if (existsSync(workdir)) rmSync(workdir, { recursive: true, force: true });
  }
}
