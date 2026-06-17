import { createTool } from "@mastra/core/tools";
import type { ZepClient } from "@getzep/zep-cloud";
import { z } from "zod";
import type { ZepThreadBinding, ZepLogger } from "./types.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";

/** Options for {@link createZepContextTool}. */
export interface ZepContextToolOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /**
   * The thread binding. `threadId` is required; the Context Block is assembled
   * from the **entire user graph** with the thread used only to scope relevance.
   */
  binding: ZepThreadBinding;
  /** Override the tool id (default `"zep-context"`). */
  id?: string;
  /** Override the tool description shown to the model. */
  description?: string;
  /**
   * Optional Zep context template ID for custom Context Block formatting.
   * When omitted, Zep's default Smart Context Assembly layout is used.
   */
  templateId?: string;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

// No input is needed: the Context Block is derived from the bound thread and
// the whole user graph. An empty object keeps the tool callable with no args.
const inputSchema = z.object({});

const outputSchema = z.object({
  context: z
    .string()
    .describe(
      "A prompt-ready Context Block of relevant facts, entities, and the user " +
        "summary assembled from the whole user graph. Empty if unavailable.",
    ),
  found: z.boolean().describe("Whether a non-empty Context Block was returned."),
});

type ContextOutput = z.infer<typeof outputSchema>;

/**
 * Build a model-callable Mastra tool that returns the **Context Block** for the
 * bound user via `thread.getUserContext`.
 *
 * This is the default recall path for conversational agents: a single call
 * returns an optimized, prompt-ready string (user summary + relevant facts and
 * entities) assembled from the *whole* user graph, with the thread's most recent
 * messages used to focus relevance. It is the tool-callable counterpart to
 * injecting context directly into the system prompt — useful when you want the
 * model to pull in user context on demand.
 *
 * A Zep failure is logged and returned as `found: false` with an empty string;
 * it never throws.
 */
export function createZepContextTool(options: ZepContextToolOptions) {
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);

  return createTool({
    id: options.id ?? "zep-context",
    description:
      options.description ??
      "Retrieve everything currently known about the user — a summary plus " +
        "relevant facts from previous conversations — to ground your response.",
    inputSchema,
    outputSchema,
    execute: async (): Promise<ContextOutput> => {
      if (!binding.threadId) {
        logger.warn("[zep-context] No threadId bound; skipping context retrieval.");
        return { context: "", found: false };
      }

      try {
        const response = await client.thread.getUserContext(
          binding.threadId,
          options.templateId ? { templateId: options.templateId } : {},
        );
        const context = response.context?.trim() ?? "";
        return { context, found: context.length > 0 };
      } catch (error) {
        logger.warn(`[zep-context] Failed to retrieve Zep context: ${errorMessage(error)}`);
        return { context: "", found: false };
      }
    },
  });
}
