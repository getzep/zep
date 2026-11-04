import { defineAgent } from "eve";
import { createAnthropic } from "@ai-sdk/anthropic";
import { getAnthropicBaseURL } from "../src/anthropic.js";

const effort = process.env.BANKING_EFFORT ?? "none";
const anthropic = createAnthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
  baseURL: getAnthropicBaseURL(),
});

export default defineAgent({
  model: anthropic(process.env.BANKING_MODEL ?? "claude-haiku-5-5"),
  modelContextWindowTokens: 200_000,
  reasoning: effort === "max" ? "xhigh" : effort as "none" | "low" | "medium" | "high",
  defaultTools: false,
});
