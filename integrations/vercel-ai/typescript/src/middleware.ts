import type { LanguageModelMiddleware } from "ai";
import type {
  LanguageModelV3CallOptions,
  LanguageModelV3Message,
  LanguageModelV3Prompt,
} from "@ai-sdk/provider";
import type { ZepClient } from "@getzep/zep-cloud";
import type { ZepLogger } from "./types.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";
import { getZepContext } from "./helpers.js";

/** Options for {@link createZepMiddleware}. */
export interface ZepMiddlewareOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /**
   * The Zep thread that scopes relevance for the injected Context Block. The
   * block is still assembled from the *whole* user graph; the thread only
   * focuses what's relevant right now.
   */
  threadId: string;
  /**
   * How to wrap the retrieved Context Block into the injected system message.
   * Receives the raw Context Block; return the full system message text.
   * Defaults to a short labeled block.
   */
  formatContext?: (context: string) => string;
  /**
   * Optional Zep context template ID for custom Context Block formatting.
   * When omitted, Zep's default Smart Context Assembly layout is used.
   */
  templateId?: string;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/** Default system-message wrapper for an injected Context Block. */
function defaultFormatContext(context: string): string {
  return (
    "The following is relevant long-term memory about the user, retrieved from " +
    "Zep. Use it to personalize and ground your response.\n\n" +
    context
  );
}

/**
 * Decide whether the current provider prompt represents a *genuine new user
 * turn* — the one moment we want to (re)fetch and inject the Context Block.
 *
 * The AI SDK tool loop calls the wrapped model once per step. The first step of
 * a turn ends with the user's message; every continuation step ends with a
 * `tool` result (or an `assistant` tool-call message). Injecting on every step
 * would re-fetch the Context Block N times per turn and stack N system messages
 * onto the prompt. So we inject only when the LAST message is from the user.
 */
function isNewUserTurn(prompt: LanguageModelV3Prompt): boolean {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const message = prompt[i];
    if (!message) continue;
    return message.role === "user";
  }
  return false;
}

/**
 * Build a Vercel AI SDK {@link LanguageModelMiddleware} that injects a Zep
 * Context Block as a leading `system` message on each genuine new user turn.
 *
 * This middleware is **context-injection only**. It does not persist anything —
 * persistence is handled once per turn from `onFinish` (see
 * {@link createZepOnFinish}). Wrap any language model with it via
 * `wrapLanguageModel`, and pair it with `createZepOnFinish` on your
 * `generateText`/`streamText` call:
 *
 * ```ts
 * import { wrapLanguageModel, generateText, stepCountIs } from "ai";
 * import { openai } from "@ai-sdk/openai";
 * import { createZepMiddleware, createZepOnFinish } from "@getzep/zep-vercel-ai";
 *
 * const model = wrapLanguageModel({
 *   model: openai("gpt-4o-mini"),
 *   middleware: createZepMiddleware({ client, threadId }),
 * });
 *
 * const { text } = await generateText({
 *   model,
 *   prompt: "What do you know about me?",
 *   stopWhen: stepCountIs(5),
 *   onFinish: createZepOnFinish({ client, threadId }),
 * });
 * ```
 *
 * **What it does**
 *
 * - `transformParams` fetches the Context Block (`thread.getUserContext`) and
 *   prepends it as a `system` message to the provider prompt — on both
 *   `generate` and `stream` calls — but **only on a genuine new user turn**
 *   (detected by the last prompt message being a `user` message). On tool-loop
 *   continuation steps (whose last message is a `tool` result or an `assistant`
 *   tool call) it injects nothing, so the Context Block is fetched at most once
 *   per turn rather than once per step.
 *
 * **Why injection-only**
 *
 * The AI SDK tool loop calls the wrapped model's `doGenerate` once per step, so
 * a `wrapGenerate`-based persistence hook would fire once per step and fragment
 * a single user+assistant turn across multiple `thread.addMessages` calls —
 * writing the model's intermediate tool-call preamble into the user graph as a
 * real assistant message. `onFinish` fires exactly once per turn with the final
 * assistant text (for both `generateText` and `streamText`), so persistence
 * lives there. See {@link createZepOnFinish}.
 *
 * All Zep failures are caught and logged (lengths only — never content/PII); a
 * Zep outage degrades to "no context" and never crashes the host call.
 */
export function createZepMiddleware(
  options: ZepMiddlewareOptions,
): LanguageModelMiddleware {
  const { client, threadId } = options;
  const logger = resolveLogger(options.logger);
  const format = options.formatContext ?? defaultFormatContext;

  return {
    specificationVersion: "v3",

    transformParams: async ({
      params,
    }: {
      type: "generate" | "stream";
      params: LanguageModelV3CallOptions;
    }): Promise<LanguageModelV3CallOptions> => {
      // Only inject on a genuine new user turn. On tool-loop continuation steps
      // (last message is a tool result or assistant tool call) we skip, so the
      // Context Block is fetched at most once per turn.
      if (!isNewUserTurn(params.prompt)) {
        return params;
      }

      try {
        const context = await getZepContext(client, threadId, {
          ...(options.templateId !== undefined ? { templateId: options.templateId } : {}),
          logger,
        });
        if (context) {
          const systemMessage: LanguageModelV3Message = {
            role: "system",
            content: format(context),
          };
          // Prepend so any existing system instructions follow the memory block.
          params.prompt.unshift(systemMessage);
        }
      } catch (error) {
        // getZepContext already degrades gracefully; this guards the unshift too.
        logger.warn(`[zep-middleware] Context injection skipped: ${errorMessage(error)}`);
      }
      return params;
    },
  };
}
