import { createTool } from "@mastra/core/tools";
import type { ZepClient } from "@getzep/zep-cloud";
import { z } from "zod";
import type { ZepBinding, ZepThreadBinding, ZepLogger } from "./types.js";
import {
  errorMessage,
  resolveGraphTarget,
  resolveLogger,
  toRoleType,
} from "./zep-utils.js";

/** Options for {@link createZepRememberTool}. */
export interface ZepRememberToolOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /**
   * The graph this tool writes to, plus the thread used to record conversation.
   *
   * - When `threadId` and `userId` are present, conversational content is
   *   persisted via `thread.addMessages` (records history *and* ingests into the
   *   user graph). Non-conversational data always uses `graph.add`.
   * - When only `graphId` (or `userId` without a thread) is present, everything
   *   is ingested via `graph.add`.
   */
  binding: ZepThreadBinding | ZepBinding;
  /** Override the tool id (default `"zep-remember"`). */
  id?: string;
  /** Override the tool description shown to the model. */
  description?: string;
  /**
   * Default `name` recorded on conversational messages (e.g. the end user's
   * real name). Passing a real name helps Zep resolve identity in the graph.
   */
  defaultMessageName?: string;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

const inputSchema = z.object({
  content: z
    .string()
    .min(1)
    .describe("The fact, message, or piece of information to remember."),
  role: z
    .string()
    .optional()
    .describe(
      "Who the content is from when it is a conversational message: 'user', " +
        "'assistant', 'system', 'tool', or 'function'. Omit for general data.",
    ),
  name: z
    .string()
    .optional()
    .describe(
      "Name of the speaker for conversational messages (e.g. the user's real name).",
    ),
});

const outputSchema = z.object({
  stored: z.boolean().describe("Whether the content was persisted to Zep."),
  message: z.string().describe("A human-readable status message."),
});

type RememberInput = z.infer<typeof inputSchema>;
type RememberOutput = z.infer<typeof outputSchema>;

function hasThread(binding: ZepThreadBinding | ZepBinding): binding is ZepThreadBinding {
  return typeof (binding as ZepThreadBinding).threadId === "string";
}

/**
 * Build a Mastra tool that **persists** information into Zep.
 *
 * Conversational content (a `role` is provided and a thread is bound) is written
 * with `thread.addMessages`, which both records conversation history and ingests
 * into the bound user graph. Everything else is ingested with `graph.add`
 * (`type: "text"`), so the agent can durably remember facts the user shares or
 * results it produces.
 *
 * Zep ingestion is **asynchronous**: a just-stored fact is not instantly
 * retrievable. The tool reports success once Zep accepts the data; design flows
 * for eventual availability.
 *
 * A Zep failure is logged and surfaced to the model as `stored: false` — it
 * never throws, so a memory outage cannot crash the host agent.
 */
export function createZepRememberTool(options: ZepRememberToolOptions) {
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);
  const target = resolveGraphTarget(binding);

  return createTool({
    id: options.id ?? "zep-remember",
    description:
      options.description ??
      "Persist a fact, preference, or message to long-term memory so it can be " +
        "recalled in future turns and future conversations.",
    inputSchema,
    outputSchema,
    execute: async (inputData: RememberInput): Promise<RememberOutput> => {
      const content = inputData.content?.trim();
      if (!content) {
        return { stored: false, message: "Nothing to remember: content was empty." };
      }
      if (!target) {
        logger.warn("[zep-remember] No userId or graphId bound; skipping persist.");
        return {
          stored: false,
          message: "Memory is not configured (no user or graph bound).",
        };
      }

      try {
        // Conversational content with a bound thread → thread.addMessages.
        if (inputData.role && hasThread(binding) && binding.userId) {
          await client.thread.addMessages(binding.threadId, {
            messages: [
              {
                role: toRoleType(inputData.role),
                content,
                name: inputData.name ?? options.defaultMessageName,
              },
            ],
          });
          return { stored: true, message: "Saved to conversation memory." };
        }

        // Everything else → graph.add as text.
        await client.graph.add({ ...target, type: "text", data: content });
        return { stored: true, message: "Saved to long-term memory." };
      } catch (error) {
        logger.warn(`[zep-remember] Failed to persist to Zep: ${errorMessage(error)}`);
        return {
          stored: false,
          message: "Could not save to memory right now; continuing without it.",
        };
      }
    },
  });
}
