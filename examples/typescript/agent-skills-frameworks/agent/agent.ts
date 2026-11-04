import { defineAgent } from "eve";
import { anthropic } from "eve/models/anthropic";

const effort = process.env.BANKING_EFFORT ?? "none";
export default defineAgent({
  model: anthropic(process.env.BANKING_MODEL ?? "claude-haiku-5-5"),
  modelContextWindowTokens: 200_000,
  reasoning: effort === "max" ? "xhigh" : effort as "none" | "low" | "medium" | "high",
  defaultTools: false,
});
