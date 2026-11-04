import { createAnthropic } from "@ai-sdk/anthropic";
import { Agent } from "@mastra/core/agent";
import { createTool } from "@mastra/core/tools";
import type { Case } from "../src/banking.js";
import { EpisodeResult } from "../src/episode.js";
import { SYSTEM, TEACHING_REQUEST, USER } from "../src/prompts.js";
import {
  BankingTools,
  ToolError,
  toolDescriptions,
  toolSchemas,
  invokeBankingTool,
  type ToolName,
} from "../src/tools.js";
import type { TraceRecorder } from "../src/zep-skills.js";

type MastraRecorder = Pick<TraceRecorder, "append">;
type TestModel = NonNullable<ConstructorParameters<typeof Agent>[0]["model"]>;

export async function runEpisode(
  caseData: Case,
  model: string,
  effort: string,
  skills: Record<string, unknown>[],
  recorder: MastraRecorder,
  teaching: boolean,
  options: { testModel?: TestModel } = {},
): Promise<EpisodeResult> {
  const tools = new BankingTools(caseData, skills);
  const agentTools = Object.fromEntries(
    (Object.keys(toolSchemas) as ToolName[]).map((name) => [
      name,
      createTool({
        id: name,
        description: toolDescriptions[name],
        inputSchema: toolSchemas[name],
        onInputAvailable: async ({ toolCallId, input }) => recorder.append(
          "tool_call",
          typeof input === "string" ? input : JSON.stringify(input),
          name,
          toolCallId,
        ),
        onOutput: async ({ toolCallId, output }) => recorder.append(
          "tool_result",
          JSON.stringify(output),
          name,
          toolCallId,
        ),
        execute: async (input) => {
          try {
            return invokeBankingTool(tools, name, input as Record<string, unknown>);
          } catch (error) {
            return { error: error instanceof ToolError ? error.message : String(error) };
          }
        },
      }),
    ]),
  );
  const client = createAnthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
  const agent = new Agent({
    id: "synthetic-banking-agent",
    name: "Synthetic banking agent",
    instructions: SYSTEM.replace("{catalog}", JSON.stringify(skills)) +
      (teaching ? `\n\n${TEACHING_REQUEST}` : ""),
    model: options.testModel ?? client(model),
    tools: agentTools,
  });
  const prompt = USER.replace("{month}", caseData.statement_month).replace("{case_id}", caseData.case_id);
  await recorder.append("input_reference", prompt);
  const result = await agent.generate(prompt, {
    maxSteps: 120,
    providerOptions: {
      anthropic: {
        disableParallelToolUse: true,
        ...(effort === "none"
          ? { thinking: { type: "disabled" } }
          : { thinking: { type: "adaptive" }, effort: effort === "max" ? "max" : effort as "low" | "medium" | "high" | "xhigh" }),
      },
    },
    onStepFinish: async (step) => {
      const text = typeof step.text === "string" ? step.text : "";
      if (text) await recorder.append("decision", text);
    },
  });
  return {
    finalAnswer: result.text,
    tokens: result.usage.totalTokens ?? 0,
    tools,
  };
}
