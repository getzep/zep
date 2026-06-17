import type { LanguageModelMiddleware } from "ai";
import type {
  LanguageModelV3CallOptions,
  LanguageModelV3Content,
  LanguageModelV3GenerateResult,
  LanguageModelV3Message,
  LanguageModelV3Prompt,
} from "@ai-sdk/provider";
import type { ZepClient } from "@getzep/zep-cloud";
import type { ZepLogger } from "./types.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";
import { getZepContext, persistZepTurn } from "./helpers.js";

/** Options for {@link createZepMiddleware}. */
export interface ZepMiddlewareOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /**
   * The Zep thread that scopes relevance and (when `persist` is on) receives
   * conversation history. The Context Block is still assembled from the *whole*
   * user graph; the thread only focuses what's relevant right now.
   */
  threadId: string;
  /**
   * Persist the user+assistant turn after a **non-streaming** `generateText`
   * call via `wrapGenerate`.
   *
   * Defaults to `false`. Note that `wrapGenerate` does **not** fire for
   * `streamText` — for streaming you must persist via `onFinish` +
   * {@link persistZepTurn} (see this package's README and the streaming
   * example). When `persist` is `true` and the middleware is used with
   * `streamText`, persistence is silently skipped at the SDK level (this is a
   * documented limitation, not a bug).
   */
  persist?: boolean;
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
  /** Speaker name recorded on the persisted user message (the user's real name). */
  userName?: string;
  /** Name recorded on the persisted assistant message. */
  assistantName?: string;
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
 * Concatenate the text parts of the most recent `user` message in a provider
 * prompt. Returns `""` when the last message is not a user message or carries no
 * text (e.g. a tool result). Non-text parts (files) are ignored.
 */
function latestUserText(prompt: LanguageModelV3Prompt): string {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const message = prompt[i];
    if (!message) continue;
    if (message.role === "user") {
      return message.content
        .filter((part): part is { type: "text"; text: string } => part.type === "text")
        .map((part) => part.text)
        .join("")
        .trim();
    }
    // Stop at the first non-user message from the end — we only persist the
    // user turn that triggered this generation.
    if (message.role === "assistant" || message.role === "tool") {
      return "";
    }
  }
  return "";
}

/** Concatenate the text content the model generated in a non-streaming result. */
function assistantText(content: Array<LanguageModelV3Content>): string {
  return content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("")
    .trim();
}

/**
 * Build a Vercel AI SDK {@link LanguageModelMiddleware} that injects a Zep
 * Context Block into every model call — and, optionally, persists the turn.
 *
 * Wrap any language model with it via `wrapLanguageModel`:
 *
 * ```ts
 * import { wrapLanguageModel, generateText } from "ai";
 * import { openai } from "@ai-sdk/openai";
 * import { createZepMiddleware } from "@getzep/zep-vercel-ai";
 *
 * const model = wrapLanguageModel({
 *   model: openai("gpt-4o-mini"),
 *   middleware: createZepMiddleware({ client, threadId, persist: true }),
 * });
 *
 * const { text } = await generateText({ model, prompt: "What do you know about me?" });
 * ```
 *
 * **What it does**
 *
 * - `transformParams` fetches the Context Block (`thread.getUserContext`) and
 *   prepends it as a `system` message to the provider prompt, on both
 *   `generate` and `stream` calls.
 * - When `persist` is enabled, `wrapGenerate` records the user+assistant turn
 *   via `thread.addMessages` after a **non-streaming** `generateText`.
 *
 * **Streaming caveat (important)**
 *
 * The AI SDK only calls `wrapGenerate` for `generateText`, never for
 * `streamText`. Context injection still works for streaming (it runs in
 * `transformParams`), but **persistence does not** — for `streamText` you must
 * persist the turn yourself from `onFinish` using {@link persistZepTurn}. This
 * limitation is by design in the SDK; the middleware does not silently pretend
 * otherwise.
 *
 * All Zep failures are caught and logged (lengths only — never content/PII); a
 * Zep outage degrades to "no context / no persistence" and never crashes the
 * host call.
 */
export function createZepMiddleware(
  options: ZepMiddlewareOptions,
): LanguageModelMiddleware {
  const { client, threadId } = options;
  const logger = resolveLogger(options.logger);
  const format = options.formatContext ?? defaultFormatContext;
  const persist = options.persist ?? false;

  return {
    specificationVersion: "v3",

    transformParams: async ({
      params,
    }: {
      type: "generate" | "stream";
      params: LanguageModelV3CallOptions;
    }): Promise<LanguageModelV3CallOptions> => {
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

    wrapGenerate: async ({
      doGenerate,
      params,
    }: {
      doGenerate: () => PromiseLike<LanguageModelV3GenerateResult>;
      params: LanguageModelV3CallOptions;
    }): Promise<LanguageModelV3GenerateResult> => {
      const result = await doGenerate();

      if (persist) {
        try {
          const user = latestUserText(params.prompt);
          const assistant = assistantText(result.content);
          if (user || assistant) {
            await persistZepTurn(
              client,
              threadId,
              {
                ...(user ? { user } : {}),
                ...(assistant ? { assistant } : {}),
                ...(options.userName !== undefined ? { userName: options.userName } : {}),
                ...(options.assistantName !== undefined
                  ? { assistantName: options.assistantName }
                  : {}),
              },
              { logger },
            );
          }
        } catch (error) {
          // persistZepTurn already degrades gracefully; double-guard anyway.
          logger.warn(`[zep-middleware] Persist skipped: ${errorMessage(error)}`);
        }
      }

      return result;
    },
  };
}
