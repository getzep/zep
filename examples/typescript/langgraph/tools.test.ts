import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { buildTools } from "./tools.ts";

describe("buildTools", () => {
  it("returns no tools when TAVILY_API_KEY is absent", () => {
    const tools = buildTools({ ...process.env, TAVILY_API_KEY: "" });
    assert.equal(tools.length, 0);
  });

  it("returns a Tavily search tool when TAVILY_API_KEY is present", () => {
    const tools = buildTools({
      ...process.env,
      TAVILY_API_KEY: "tvly-test-key",
    });
    assert.equal(tools.length, 1);
    assert.match(tools[0].name, /tavily/i);
  });
});
