import { describe, expect, it } from "vitest";
import { createOpenAI } from "@ai-sdk/openai";
import { generateText, stepCountIs, tool } from "ai";
import { z } from "zod";
import { exampleModel, exampleProviderOptions } from "../examples/model.js";

const usage = {
  input_tokens: 1,
  output_tokens: 1,
  input_tokens_details: { cached_tokens: 0 },
  output_tokens_details: { reasoning_tokens: 0 },
};

function responseBody(id: string, output: unknown[]) {
  return {
    id,
    object: "response",
    created_at: 1,
    model: "gpt-6-luna",
    status: "completed",
    incomplete_details: null,
    output,
    usage,
  };
}

describe("example model", () => {
  it("calls the Responses API at low reasoning and replays reasoning items in the tool loop", async () => {
    const requests: { url: string; body: Record<string, unknown> }[] = [];
    const replies = [
      responseBody("resp_1", [
        {
          type: "reasoning",
          id: "rs_1",
          encrypted_content: "encrypted-1",
          summary: [],
        },
        {
          type: "function_call",
          id: "fc_1",
          call_id: "call_1",
          name: "lookup",
          arguments: "{}",
          status: "completed",
        },
      ]),
      responseBody("resp_2", [
        {
          type: "message",
          id: "msg_1",
          role: "assistant",
          status: "completed",
          content: [{ type: "output_text", text: "Portland", annotations: [] }],
        },
      ]),
    ];
    const fetch = async (url: string | URL | Request, init?: RequestInit) => {
      requests.push({ url: String(url), body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(replies[requests.length - 1]), {
        headers: { "content-type": "application/json" },
      });
    };
    const provider = createOpenAI({
      apiKey: "test-key",
      baseURL: "https://api.openai.com/v1",
      fetch,
    });

    const { text } = await generateText({
      model: exampleModel(provider),
      tools: {
        lookup: tool({
          inputSchema: z.object({}),
          execute: async () => ({ city: "Portland" }),
        }),
      },
      stopWhen: stepCountIs(3),
      providerOptions: exampleProviderOptions,
      prompt: "Where do I live?",
    });

    expect(text).toBe("Portland");
    expect(requests).toHaveLength(2);
    for (const request of requests) {
      expect(request.url).toBe("https://api.openai.com/v1/responses");
      expect(request.body.model).toBe("gpt-6-luna");
      expect(request.body.reasoning).toEqual({ effort: "low" });
      expect(request.body.store).toBe(false);
      expect(request.body.include).toContain("reasoning.encrypted_content");
      expect(request.body).not.toHaveProperty("temperature");
      expect(request.body).not.toHaveProperty("top_p");
    }
    const replayed = requests[1]!.body.input as Record<string, unknown>[];
    expect(replayed).toContainEqual(
      expect.objectContaining({
        type: "reasoning",
        id: "rs_1",
        encrypted_content: "encrypted-1",
      }),
    );
    expect(replayed).toContainEqual(
      expect.objectContaining({
        type: "function_call_output",
        call_id: "call_1",
      }),
    );
  });
});
