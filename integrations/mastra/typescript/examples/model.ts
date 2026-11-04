export const EXAMPLE_MODEL = "openai/gpt-6-luna";

/**
 * Mastra routes `openai/...` models to the OpenAI Responses API. The OpenAI
 * provider that Mastra bundles does not detect gpt-6 models as reasoning
 * models, so `forceReasoning: true` makes it send the reasoning effort.
 */
export const exampleProviderOptions = {
  openai: {
    reasoningEffort: "low",
    forceReasoning: true,
  },
};
