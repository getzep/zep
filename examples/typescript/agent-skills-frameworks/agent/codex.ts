import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Codex, type ThreadEvent } from "@openai/codex-sdk";
import type { Case } from "../src/banking.js";
import { SYSTEM, userPrompt } from "../src/prompts.js";
import { BankingTools, toolSchemas } from "../src/tools.js";
import type { EpisodeResult } from "../src/episode.js";
import type { TraceRecorder } from "../src/zep-skills.js";

type CodexRecorder = Pick<TraceRecorder, "append">;

export type CodexMapping =
  | { kind: "trace"; eventType: string; content: string; toolName?: string; toolCallId?: string }
  | { kind: "answer"; answer: string }
  | { kind: "tokens"; tokens: number }
  | { kind: "failure"; message: string };

export function createCodexProviderConfig(baseUrl = process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1") {
  return {
    model_provider: "openai_http",
    model_providers: {
      openai_http: {
        name: "OpenAI",
        base_url: baseUrl,
        env_key: "OPENAI_API_KEY",
        wire_api: "responses",
        supports_websockets: false,
      },
    },
  };
}

export function createCodexBankingMcpConfig() {
  return {
    default_tools_approval_mode: "approve",
    enabled_tools: Object.keys(toolSchemas),
  };
}

export function mapCodexEvent(event: ThreadEvent): CodexMapping | null {
  if (event.type === "turn.failed") return { kind: "failure", message: event.error.message };
  if (event.type === "error") return { kind: "failure", message: event.message };
  if (event.type === "turn.completed") {
    const usage = event.usage;
    return { kind: "tokens", tokens: usage.input_tokens + usage.output_tokens + usage.cached_input_tokens + usage.cache_write_input_tokens };
  }
  if (!("item" in event)) return null;
  const item = event.item;
  if (item.type === "command_execution" || item.type === "file_change" || item.type === "web_search") {
    return { kind: "failure", message: `Codex emitted forbidden ${item.type} activity.` };
  }
  if (item.type === "agent_message" && event.type === "item.completed") {
    return { kind: "answer", answer: item.text };
  }
  if (item.type !== "mcp_tool_call") return null;
  const toolName = item.tool.replace(/^.*__/, "");
  if (toolName === "list_mcp_resources" || toolName === "list_mcp_resource_templates") return null;
  if (event.type === "item.started") {
    return { kind: "trace", eventType: "tool_call", content: JSON.stringify(item.arguments), toolName, toolCallId: item.id };
  }
  if (event.type === "item.completed") {
    const text = item.error?.message ??
      item.result?.content.map((block) => block.type === "text" ? block.text : "").join("") ??
      "MCP tool call returned no content.";
    return { kind: "trace", eventType: "tool_result", content: text, toolName, toolCallId: item.id };
  }
  return null;
}

export async function runEpisode(
  caseData: Case,
  model: string,
  effort: string,
  skills: Record<string, unknown>[],
  recorder: CodexRecorder,
  teaching: boolean,
): Promise<EpisodeResult> {
  const workdir = mkdtempSync(join(tmpdir(), "zep-banking-codex-"));
  const inputPath = join(workdir, "input.json");
  const statePath = join(workdir, "state.json");
  writeFileSync(inputPath, JSON.stringify({ case: caseData, skills }));
  const serverPath = resolve("agent/codex-mcp-server.ts");
  const packageDir = process.cwd();
  const codex = new Codex({
    apiKey: process.env.OPENAI_API_KEY,
    env: {
      ...process.env as Record<string, string>,
      BANKING_INPUT_PATH: inputPath,
      BANKING_STATE_PATH: statePath,
    },
    config: {
      ...createCodexProviderConfig(),
      developer_instructions: SYSTEM.replace("{catalog}", JSON.stringify(skills)),
      mcp_servers: {
        banking: {
          ...createCodexBankingMcpConfig(),
          command: process.execPath,
          args: ["--import", "tsx", serverPath],
          cwd: packageDir,
          env: { BANKING_INPUT_PATH: inputPath, BANKING_STATE_PATH: statePath },
        },
      },
      features: {
        shell_tool: false,
        code_mode: false,
        // The banking MCP server needs this host; code-mode tools remain disabled.
        code_mode_host: true,
        code_mode_interrupt: false,
        code_mode_only: false,
        code_mode_only_strict_3p_tools: false,
        code_mode_prewarm: false,
        code_mode_tool_search: false,
        computer_use: false,
        browser_use: false,
        browser_use_external: false,
        in_app_browser: false,
        image_generation: false,
        apps: false,
        view_image: false,
        sleep_tool: false,
        standalone_web_search: false,
        web_search_request: false,
        multi_agent: false,
        multi_agent_v2: false,
        multi_agent_v2_dynamic_tools: false,
        skill_search: false,
        plugins: false,
        plugin_discovery_prefetch: false,
      },
    },
  });
  const thread = codex.startThread({
    model,
    workingDirectory: workdir,
    skipGitRepoCheck: true,
    sandboxMode: "read-only",
    approvalPolicy: "never",
    networkAccessEnabled: false,
    webSearchMode: "disabled",
    webSearchEnabled: false,
    modelReasoningEffort: effort as "low" | "medium" | "high" | "xhigh" | "max" | "ultra",
  });
  const prompt = userPrompt(caseData, teaching);
  await recorder.append("input_reference", prompt);
  const streamed = await thread.runStreamed(prompt);
  let finalAnswer = "";
  let tokens = 0;
  for await (const event of streamed.events) {
    const mapped = mapCodexEvent(event);
    if (!mapped) continue;
    if (mapped.kind === "failure") throw new Error(mapped.message);
    if (mapped.kind === "tokens") tokens = mapped.tokens;
    if (mapped.kind === "answer") {
      finalAnswer = mapped.answer;
      await recorder.append("decision", mapped.answer);
    }
    if (mapped.kind === "trace") {
      if (mapped.toolName && !Object.keys(toolSchemas).includes(mapped.toolName)) {
        throw new Error(`Codex called an unknown banking tool: ${mapped.toolName}.`);
      }
      if (mapped.toolName) await recorder.append(mapped.eventType, mapped.content, mapped.toolName, mapped.toolCallId);
    }
  }
  const state = JSON.parse(readFileSync(statePath, "utf8")) as { calls: BankingTools["calls"]; database: BankingTools["database"] };
  const tools = new BankingTools(caseData, skills);
  tools.calls.push(...state.calls);
  tools.database.credits.push(...state.database.credits);
  tools.database.reports.push(...state.database.reports);
  return { finalAnswer, tokens, tools };
}
