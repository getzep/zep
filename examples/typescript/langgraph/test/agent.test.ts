import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { HumanMessage, ToolMessage } from "@langchain/core/messages";

process.env.OPENAI_API_KEY = "test-key";
process.env.OPENAI_BASE_URL = "https://api.openai.com/v1";
delete process.env.TAVILY_API_KEY;
const { baseModel } = await import("../agent.ts");

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

const usage = {
  input_tokens: 1,
  output_tokens: 1,
  total_tokens: 2,
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

test("agent model calls the Responses API at low reasoning and replays reasoning items", async () => {
  const requests: { url: string; body: Record<string, unknown> }[] = [];
  const replies = [
    responseBody("resp_1", [
      { type: "reasoning", id: "rs_1", encrypted_content: "encrypted-1", summary: [] },
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
  globalThis.fetch = async (url: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify(replies[requests.length - 1]), {
      headers: { "content-type": "application/json" },
    });
  };
  const lookup = {
    type: "function" as const,
    function: {
      name: "lookup",
      description: "Look up the user's city.",
      parameters: { type: "object", properties: {} },
    },
  };
  const model = baseModel.bindTools([lookup]);

  const question = new HumanMessage("Where do I live?");
  const first = await model.invoke([question]);
  assert.equal(first.tool_calls?.[0]?.id, "call_1");
  const second = await model.invoke([
    question,
    first,
    new ToolMessage({ content: "Portland", tool_call_id: "call_1" }),
  ]);

  assert.equal(second.text, "Portland");
  assert.equal(requests.length, 2);
  for (const request of requests) {
    assert.equal(request.url, "https://api.openai.com/v1/responses");
    assert.equal(request.body.model, "gpt-6-luna");
    assert.deepEqual(request.body.reasoning, { effort: "low" });
    assert.equal("temperature" in request.body, false);
    assert.equal("top_p" in request.body, false);
  }
  const replayed = requests[1]!.body.input as Record<string, unknown>[];
  assert.ok(
    replayed.some((item) => item.type === "reasoning" && item.id === "rs_1"),
    JSON.stringify(replayed),
  );
  assert.ok(replayed.some((item) => item.type === "function_call_output" && item.call_id === "call_1"));
});
