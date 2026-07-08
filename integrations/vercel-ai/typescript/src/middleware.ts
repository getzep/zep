import type { LanguageModelMiddleware } from "ai";
import type {
  LanguageModelV3CallOptions,
  LanguageModelV3GenerateResult,
  LanguageModelV3Message,
  LanguageModelV3Prompt,
  LanguageModelV3StreamPart,
  LanguageModelV3StreamResult,
} from "@ai-sdk/provider";
import type { ZepClient } from "@getzep/zep-cloud";
import type { ZepContextBuilder, ZepLogger } from "./types.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";
import { getZepContext, persistZepTurn } from "./helpers.js";

/**
 * The canonical Context Block wrapper template, shared verbatim across every
 * Zep framework integration (Python, Go, TypeScript) — keep them in sync.
 * Rendered via plain string replacement
 * (`template.split("{context}").join(contextText)`), never a regex or
 * template-literal execution of user content — so a Context Block or custom
 * template containing `{`/`}`/`%`/`$` is always safe to inject.
 *
 * **Changed in 0.2.0 (breaking):** the default injected wording is now this
 * canonical text instead of the 0.1.x wording. Pass `formatContext` to
 * restore the old output — see the CHANGELOG migration recipe.
 */
export const DEFAULT_CONTEXT_TEMPLATE =
  "The following context is retrieved from Zep, the agent's long-term memory. " +
  "It contains relevant facts, entities, and prior knowledge about the user. " +
  "Use it to inform your responses.\n\n" +
  "<ZEP_CONTEXT>\n" +
  "{context}\n" +
  "</ZEP_CONTEXT>";

/**
 * Guaranteed-persistence configuration for {@link ZepMiddlewareOptions.persist}.
 *
 * `true` persists with no speaker names; pass an object to record names on the
 * persisted messages (e.g. the end user's real name, to help Zep resolve
 * identity).
 */
export interface ZepPersistOptions {
  /** Speaker name recorded on the persisted user message. */
  userName?: string;
  /** Name recorded on the persisted assistant message. */
  assistantName?: string;
}

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
   * The Zep user ID for this turn. Not required for injection or persistence
   * (both are scoped by `threadId`), but handed to a custom
   * {@link contextBuilder} for power users who need it.
   */
  userId?: string;
  /**
   * How to wrap the retrieved Context Block into the injected system message.
   * Receives the raw Context Block; return the full system message text.
   * Defaults to wrapping {@link DEFAULT_CONTEXT_TEMPLATE} around the block.
   */
  formatContext?: (context: string) => string;
  /**
   * Optional Zep context template ID for custom Context Block formatting.
   * When omitted, Zep's default Smart Context Assembly layout is used.
   * Ignored when {@link contextBuilder} is set.
   */
  templateId?: string;
  /**
   * Replace the default `thread.getUserContext` retrieval with a custom
   * builder. Runs inside the same try/catch as the default retrieval path —
   * a rejection is logged and degrades to "no context injected" for that
   * turn, exactly like a `getZepContext` failure. The builder's result is
   * still wrapped by `formatContext`/{@link DEFAULT_CONTEXT_TEMPLATE}; return
   * `undefined` to inject nothing.
   *
   * This middleware only retrieves — it does not gather/persist inside
   * `transformParams`. If you need persistence, use the {@link persist}
   * option (the separate `wrapGenerate`/`wrapStream` hooks), not the builder.
   */
  contextBuilder?: ZepContextBuilder;
  /**
   * Opt-in **guaranteed persistence loop**. When unset (the default), this
   * middleware is injection-only — `wrapGenerate`/`wrapStream` are `undefined`
   * on the returned middleware, and you must persist yourself (e.g. via
   * {@link createZepOnFinish}).
   *
   * When set (`true`, or an options object to record speaker names), the
   * middleware also wraps `wrapGenerate`/`wrapStream`: after the model's
   * final step in a turn (`finishReason !== "tool-calls"`), it fires a
   * fire-and-forget `thread.addMessages` call with the user's message (the
   * last user turn in `params.prompt`) and the assistant's final text.
   * Persistence never blocks or throws into the host call — failures are
   * logged via `logger`.
   *
   * **Use one or the other:** enabling `persist` here AND wiring
   * `createZepOnFinish` on the same call double-persists every turn (two
   * `thread.addMessages` calls, one from each path). Pick exactly one.
   */
  persist?: boolean | ZepPersistOptions;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/** Default system-message wrapper for an injected Context Block. */
function defaultFormatContext(context: string): string {
  return DEFAULT_CONTEXT_TEMPLATE.split("{context}").join(context);
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
 * Extract the concatenated text of the LAST `user` message in a V3 prompt.
 *
 * Used by the guaranteed-persist `wrapGenerate`/`wrapStream` hooks to recover
 * "what the user said this turn" from `params.prompt` — there is no separate
 * `onFinish`-style event carrying it in the wrap hooks.
 */
function lastUserMessageText(prompt: LanguageModelV3Prompt): string {
  for (let i = prompt.length - 1; i >= 0; i--) {
    const message = prompt[i];
    if (!message || message.role !== "user") continue;
    return message.content
      .filter((part): part is { type: "text"; text: string } => part.type === "text")
      .map((part) => part.text)
      .join("");
  }
  return "";
}

/** Extract the concatenated assistant text from a V3 generate result's content. */
function assistantTextFromContent(content: LanguageModelV3GenerateResult["content"]): string {
  return content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/** Whether a V3 finish reason represents the end of the turn (not a tool-call step). */
function isEndOfTurn(finishReason: LanguageModelV3GenerateResult["finishReason"]): boolean {
  return finishReason.unified !== "tool-calls";
}

/**
 * Fire-and-forget persistence for the guaranteed-persist loop. Never throws —
 * `persistZepTurn` already catches and logs internally, but we also guard the
 * call itself since this runs detached from the caller's control flow.
 */
function persistTurn(
  client: ZepClient,
  threadId: string,
  userText: string,
  assistantText: string,
  persistOptions: ZepPersistOptions,
  logger: ZepLogger,
): void {
  void persistZepTurn(
    client,
    threadId,
    {
      ...(userText ? { user: userText } : {}),
      ...(assistantText ? { assistant: assistantText } : {}),
      ...(persistOptions.userName !== undefined ? { userName: persistOptions.userName } : {}),
      ...(persistOptions.assistantName !== undefined
        ? { assistantName: persistOptions.assistantName }
        : {}),
    },
    { logger },
  ).catch((error: unknown) => {
    // persistZepTurn never rejects (it catches internally and returns null),
    // but guard here too since this call is detached from the caller.
    logger.warn(`[zep-middleware] Persist failed: ${errorMessage(error)}`);
  });
}

/**
 * Build a Vercel AI SDK {@link LanguageModelMiddleware} that injects a Zep
 * Context Block as a leading `system` message on each genuine new user turn,
 * with optional guaranteed persistence.
 *
 * ```ts
 * import { wrapLanguageModel, generateText, stepCountIs } from "ai";
 * import { openai } from "@ai-sdk/openai";
 * import { createZepMiddleware } from "@getzep/zep-vercel-ai";
 *
 * const model = wrapLanguageModel({
 *   model: openai("gpt-4o-mini"),
 *   // `persist: true` guarantees the turn is written to Zep — no separate
 *   // onFinish wiring required.
 *   middleware: createZepMiddleware({ client, threadId, persist: true }),
 * });
 *
 * const { text } = await generateText({
 *   model,
 *   prompt: "What do you know about me?",
 *   stopWhen: stepCountIs(5),
 * });
 * ```
 *
 * **Injection** — `transformParams` fetches the Context Block
 * (`thread.getUserContext`, or a custom {@link ZepMiddlewareOptions.contextBuilder})
 * and prepends it as a `system` message to the provider prompt — on both
 * `generate` and `stream` calls — but **only on a genuine new user turn**
 * (detected by the last prompt message being a `user` message). On tool-loop
 * continuation steps (whose last message is a `tool` result or an `assistant`
 * tool call) it injects nothing, so the Context Block is fetched at most once
 * per turn rather than once per step.
 *
 * **Persistence** — opt-in via {@link ZepMiddlewareOptions.persist}. When
 * unset (the default), this middleware is **injection only**:
 * `wrapGenerate`/`wrapStream` are `undefined` on the returned middleware, and
 * you must persist yourself (e.g. {@link createZepOnFinish}). When set,
 * `wrapGenerate`/`wrapStream` persist the user's message and the final
 * assistant text exactly once per turn — after the LAST step
 * (`finishReason !== "tool-calls"`), never on intermediate tool-call steps.
 * Persistence is fire-and-forget: it never blocks or throws into the host
 * call. **Use one or the other** — enabling `persist` here AND wiring
 * `createZepOnFinish` on the same call double-persists every turn.
 *
 * All Zep failures are caught and logged (lengths only — never content/PII); a
 * Zep outage degrades to "no context" / "not persisted" and never crashes the
 * host call.
 */
export function createZepMiddleware(options: ZepMiddlewareOptions): LanguageModelMiddleware {
  const { client, threadId } = options;
  const logger = resolveLogger(options.logger);
  const format = options.formatContext ?? defaultFormatContext;
  const persistOptions: ZepPersistOptions | undefined =
    options.persist === true ? {} : options.persist || undefined;

  const transformParams = async ({
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
      const context = options.contextBuilder
        ? await options.contextBuilder({
            client,
            ...(options.userId !== undefined ? { userId: options.userId } : {}),
            threadId,
            userMessage: lastUserMessageText(params.prompt),
            params,
          })
        : await getZepContext(client, threadId, {
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
      // getZepContext already degrades gracefully; this guards the unshift
      // (and a rejecting contextBuilder) too.
      logger.warn(`[zep-middleware] Context injection skipped: ${errorMessage(error)}`);
    }
    return params;
  };

  if (!persistOptions) {
    // Injection only: no wrapGenerate/wrapStream, matching today's contract.
    return {
      specificationVersion: "v3",
      transformParams,
    };
  }

  const wrapGenerate = async ({
    doGenerate,
    params,
  }: {
    doGenerate: () => PromiseLike<LanguageModelV3GenerateResult>;
    doStream: () => PromiseLike<LanguageModelV3StreamResult>;
    params: LanguageModelV3CallOptions;
    model: unknown;
  }): Promise<LanguageModelV3GenerateResult> => {
    const result = await doGenerate();
    if (isEndOfTurn(result.finishReason)) {
      const userText = lastUserMessageText(params.prompt);
      const assistantText = assistantTextFromContent(result.content);
      persistTurn(client, threadId, userText, assistantText, persistOptions, logger);
    }
    return result;
  };

  const wrapStream = async ({
    doStream,
    params,
  }: {
    doGenerate: () => PromiseLike<LanguageModelV3GenerateResult>;
    doStream: () => PromiseLike<LanguageModelV3StreamResult>;
    params: LanguageModelV3CallOptions;
    model: unknown;
  }): Promise<LanguageModelV3StreamResult> => {
    const result = await doStream();
    let assistantText = "";

    const passthrough = new TransformStream<LanguageModelV3StreamPart, LanguageModelV3StreamPart>({
      transform(part, controller) {
        if (part.type === "text-delta") {
          assistantText += part.delta;
        } else if (part.type === "finish" && isEndOfTurn(part.finishReason)) {
          const userText = lastUserMessageText(params.prompt);
          persistTurn(client, threadId, userText, assistantText, persistOptions, logger);
        }
        controller.enqueue(part);
      },
    });

    return {
      ...result,
      stream: result.stream.pipeThrough(passthrough),
    };
  };

  return {
    specificationVersion: "v3",
    transformParams,
    wrapGenerate,
    wrapStream,
  };
}
