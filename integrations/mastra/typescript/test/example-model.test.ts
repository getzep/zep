import { afterEach, describe, expect, it, vi } from "vitest";
import { Agent } from "@mastra/core/agent";
import { EXAMPLE_MODEL, exampleProviderOptions } from "../examples/model.js";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("example model", () => {
  it("calls the OpenAI Responses API with gpt-6-luna at low reasoning", async () => {
    vi.stubEnv("OPENAI_API_KEY", "test-key");
    vi.stubEnv("OPENAI_BASE_URL", "https://api.openai.com/v1");
    const requests: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      "fetch",
      async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({
          url: String(url),
          body: JSON.parse(String(init?.body)),
        });
        return new Response(JSON.stringify({ error: { message: "mocked" } }), {
          status: 400,
        });
      },
    );

    const agent = new Agent({
      id: "example-agent",
      name: "Example Agent",
      instructions: "You are a helpful assistant.",
      model: EXAMPLE_MODEL,
      defaultOptions: { providerOptions: exampleProviderOptions },
    });
    await agent.generate("Hello").catch(() => undefined);

    expect(requests.length).toBeGreaterThan(0);
    const [request] = requests;
    expect(request!.url).toBe("https://api.openai.com/v1/responses");
    expect(request!.body.model).toBe("gpt-6-luna");
    expect(request!.body.reasoning).toEqual({ effort: "low" });
    expect(request!.body).not.toHaveProperty("temperature");
    expect(request!.body).not.toHaveProperty("top_p");
  });
});
