import { afterEach, describe, expect, it, vi } from "vitest";
import { getAnthropicBaseURL } from "../src/anthropic.js";

describe("Anthropic base URL", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("adds /v1 to a custom base URL", () => {
    vi.stubEnv("ANTHROPIC_BASE_URL", "http://127.0.0.1:14100/anthropic");
    expect(getAnthropicBaseURL()).toBe("http://127.0.0.1:14100/anthropic/v1");
  });

  it("does not add a second /v1", () => {
    vi.stubEnv("ANTHROPIC_BASE_URL", "http://127.0.0.1:14100/anthropic/v1/");
    expect(getAnthropicBaseURL()).toBe("http://127.0.0.1:14100/anthropic/v1");
  });

  it("returns undefined when no base URL is set", () => {
    vi.stubEnv("ANTHROPIC_BASE_URL", "");
    expect(getAnthropicBaseURL()).toBeUndefined();
  });
});
