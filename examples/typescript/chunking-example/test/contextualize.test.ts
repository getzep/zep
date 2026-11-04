import assert from "node:assert/strict";
import { test } from "node:test";
import type OpenAI from "openai";
import { contextualizeChunk } from "../src/index";

test("contextualizeChunk calls gpt-6-luna at low reasoning", async () => {
  const requests: Record<string, unknown>[] = [];
  const client = {
    chat: {
      completions: {
        create: async (params: Record<string, unknown>) => {
          requests.push(params);
          return { choices: [{ message: { content: "Context." } }] };
        },
      },
    },
  } as unknown as OpenAI;

  const result = await contextualizeChunk(client, "Full document.", "Chunk.");

  assert.equal(result, "Context.\n\n---\n\nChunk.");
  assert.equal(requests.length, 1);
  assert.equal(requests[0]!.model, "gpt-6-luna");
  assert.equal(requests[0]!.reasoning_effort, "low");
  assert.equal("temperature" in requests[0]!, false);
  assert.equal("tools" in requests[0]!, false);
});
