import {
  openai,
  type OpenAILanguageModelResponsesOptions,
  type OpenAIProvider,
} from "@ai-sdk/openai";

export const EXAMPLE_MODEL_ID = "gpt-6-luna";

/**
 * gpt-6-luna supports tool calls with reasoning only on the Responses API.
 * `store: false` makes the AI SDK request encrypted reasoning items and send
 * them back on each tool-loop step, so the examples also work for OpenAI Zero
 * Data Retention (ZDR) organizations.
 */
export const exampleProviderOptions = {
  openai: {
    reasoningEffort: "low",
    store: false,
  } satisfies OpenAILanguageModelResponsesOptions,
};

export function exampleModel(
  provider: OpenAIProvider = openai,
): ReturnType<OpenAIProvider["responses"]> {
  return provider.responses(EXAMPLE_MODEL_ID);
}
