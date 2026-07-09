/**
 * Automatic memory loop via Mastra input/output processors.
 *
 * Unlike the tool-only surface in {@link "./toolset.js"}, these processors sit
 * directly in an `Agent`'s `inputProcessors` / `outputProcessors` pipeline and
 * require no model tool-calling round-trip:
 *
 * - {@link ZepInputProcessor} runs before the model call, retrieves a Zep
 *   Context Block for the latest user message, and injects it as a system
 *   message.
 * - {@link ZepOutputProcessor} runs after the model call, persisting the
 *   user/assistant turn to the bound Zep thread.
 *
 * Input and output processors sit on opposite sides of the model call, so
 * running both is naturally concurrency-safe — the same ADK-parity guarantee
 * ADK's `beforeModelCallback`/`afterModelCallback` pair provides, for free.
 *
 * Every Zep call is wrapped so a failure is logged and the turn proceeds
 * unaffected: the input processor never withholds or mutates the user's
 * messages and never calls `abort()`; the output processor never throws back
 * into the agent loop.
 */

import type { ZepClient } from "@getzep/zep-cloud";
import type { MastraDBMessage } from "@mastra/core/agent/message-list";
import type {
  ProcessInputArgs,
  ProcessInputResultWithSystemMessages,
  ProcessOutputResultArgs,
} from "@mastra/core/processors";
import type { ResolvedZepIdentity, ZepIdentityResolver, ZepLogger } from "./types.js";
import {
  errorMessage,
  MESSAGE_MAX_CHARS,
  resolveLogger,
  truncateForZep,
} from "./zep-utils.js";

export type { ResolvedZepIdentity, ZepIdentityResolver } from "./types.js";

/**
 * Default template used to wrap the retrieved Zep Context Block before
 * injecting it as a system message. Rendered via plain string replacement
 * (`template.split("{context}").join(contextText)`, i.e. every literal
 * `{context}` occurrence is replaced) — never a regex or
 * `String.prototype.replace` pattern — so context text or a custom template
 * containing `{`/`}`/`%`/`$` is always safe to inject.
 *
 * This exact string is canonical across Zep's Python, Go, and TypeScript
 * framework integrations — keep them in sync.
 */
export const DEFAULT_CONTEXT_TEMPLATE =
  "The following context is retrieved from Zep, the agent's long-term memory. " +
  "It contains relevant facts, entities, and prior knowledge about the user. " +
  "Use it to inform your responses.\n\n" +
  "<ZEP_CONTEXT>\n" +
  "{context}\n" +
  "</ZEP_CONTEXT>";

/** Render `contextBlock` into `template`, replacing every `{context}` occurrence. */
function renderTemplate(contextBlock: string, template: string): string {
  return template.split("{context}").join(contextBlock);
}

/** Input handed to a custom {@link ZepContextBuilder}. */
export interface ZepContextBuilderInput {
  /** The `ZepClient` in use by the integration. */
  client: ZepClient;
  /** The resolved Zep user ID for this turn, if any. */
  userId?: string;
  /** The resolved Zep thread ID for this turn. */
  threadId: string;
  /** The latest user message text for this turn. */
  userMessage: string;
  /** The Mastra `requestContext` for this turn, if any. */
  requestContext?: unknown;
}

/**
 * A custom context builder function, replacing `thread.getUserContext` as the
 * source of the injected Context Block.
 *
 * Error semantics: if the builder rejects or resolves to `undefined`, no
 * system message is injected for this turn — the input processor never
 * throws and never calls `abort()`.
 */
export type ZepContextBuilder = (
  input: ZepContextBuilderInput,
) => Promise<string | undefined>;

/** Extract the concatenated text of every `type: "text"` part on a message. */
function extractMessageText(message: unknown): string {
  const content = (message as { content?: { parts?: unknown[] } } | undefined)?.content;
  const parts = content?.parts;
  if (!Array.isArray(parts)) return "";
  const texts: string[] = [];
  for (const part of parts) {
    const p = part as { type?: string; text?: string };
    if (p?.type === "text" && typeof p.text === "string") {
      texts.push(p.text);
    }
  }
  return texts.join("");
}

/** Find the latest `role: "user"` message and return its text, or `""` if none. */
function extractLatestUserText(messages: readonly unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as { role?: string } | undefined;
    if (message?.role === "user") {
      return extractMessageText(message).trim();
    }
  }
  return "";
}

/** Options shared by {@link ZepInputProcessor} and {@link ZepOutputProcessor}. */
export interface ZepProcessorSharedOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /** Default Zep user ID, used when {@link resolveIdentity} is unset or omits it. */
  userId?: string;
  /** Default Zep thread ID, used when {@link resolveIdentity} is unset or omits it. */
  threadId?: string;
  /**
   * Resolve identity per call from the Mastra `requestContext`, overriding
   * the constructor-bound `userId`/`threadId` when it returns a value.
   */
  resolveIdentity?: ZepIdentityResolver;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/** Options for {@link ZepInputProcessor}. */
export interface ZepInputProcessorOptions extends ZepProcessorSharedOptions {
  /**
   * Optional Zep context template ID, forwarded to `thread.getUserContext`.
   * Ignored when {@link contextBuilder} is set.
   */
  templateId?: string;
  /**
   * Replace `thread.getUserContext` with a custom async builder for the
   * Context Block.
   */
  contextBuilder?: ZepContextBuilder;
  /**
   * Template used to wrap the retrieved context before injecting it as a
   * system message. Must contain a literal `{context}` placeholder.
   * Defaults to {@link DEFAULT_CONTEXT_TEMPLATE}. Ignored when
   * {@link formatContext} is set.
   */
  contextTemplate?: string;
  /**
   * Full override for formatting the retrieved context into the injected
   * system message text. Takes precedence over {@link contextTemplate}.
   */
  formatContext?: (context: string) => string;
}

/**
 * Resolve identity for this call: `resolveIdentity` result wins, falling back
 * to constructor binding. The resolver may be async, so its result is awaited.
 */
async function resolveCallIdentity(
  options: ZepProcessorSharedOptions,
  requestContext: unknown,
): Promise<ResolvedZepIdentity> {
  const override = await options.resolveIdentity?.(requestContext);
  return {
    userId: override?.userId ?? options.userId,
    threadId: override?.threadId ?? options.threadId,
  };
}

/**
 * Mastra input processor: retrieves a Zep Context Block for the latest user
 * message and injects it as a system message, before the model is called.
 *
 * Missing `threadId` (after identity resolution) or any Zep failure degrades
 * gracefully — messages and system messages pass through unchanged, a
 * warning is logged, and `abort()` is never called.
 */
export class ZepInputProcessor {
  readonly id = "zep-context";
  readonly name = "zep-context";

  private readonly options: ZepInputProcessorOptions;
  private readonly logger: ZepLogger;

  constructor(options: ZepInputProcessorOptions) {
    this.options = options;
    this.logger = resolveLogger(options.logger);
  }

  async processInput(
    args: ProcessInputArgs,
  ): Promise<ProcessInputResultWithSystemMessages> {
    const { messages, systemMessages } = args;
    const passthrough = { messages, systemMessages };

    const userMessage = extractLatestUserText(messages);
    if (!userMessage) {
      return passthrough;
    }

    const identity = await resolveCallIdentity(this.options, args.requestContext);
    if (!identity.threadId) {
      this.logger.warn(
        "[zep-context] No threadId resolved for this call; skipping context injection.",
      );
      return passthrough;
    }

    let context: string | undefined;
    try {
      if (this.options.contextBuilder) {
        context = await this.options.contextBuilder({
          client: this.options.client,
          userId: identity.userId,
          threadId: identity.threadId,
          userMessage,
          requestContext: args.requestContext,
        });
      } else {
        const response = await this.options.client.thread.getUserContext(
          identity.threadId,
          this.options.templateId ? { templateId: this.options.templateId } : {},
        );
        context = response.context ?? undefined;
      }
    } catch (error) {
      this.logger.warn(
        `[zep-context] Failed to retrieve Zep context: ${errorMessage(error)}`,
      );
      return passthrough;
    }

    const trimmed = context?.trim();
    if (!trimmed) {
      return passthrough;
    }

    const rendered = this.options.formatContext
      ? this.options.formatContext(trimmed)
      : renderTemplate(trimmed, this.options.contextTemplate ?? DEFAULT_CONTEXT_TEMPLATE);

    return {
      messages,
      systemMessages: [...systemMessages, { role: "system", content: rendered }],
    };
  }
}

/** Options for {@link ZepOutputProcessor}. */
export type ZepOutputProcessorOptions = ZepProcessorSharedOptions;

/**
 * The assistant text of the final step that produced any, or `""`.
 *
 * `result.text` is the text of ALL steps joined with no separator, so in a
 * multi-step tool loop it concatenates tool-call preamble with the final
 * answer. Falls back to `result.text` only when no step carries text (e.g. a
 * caller that does not populate `steps`).
 */
function extractFinalStepText(result: ProcessOutputResultArgs["result"]): string {
  const steps = Array.isArray(result.steps) ? result.steps : [];
  for (let i = steps.length - 1; i >= 0; i--) {
    const text = steps[i]?.text?.trim();
    if (text) return text;
  }
  return result.text?.trim() ?? "";
}

/**
 * Mastra output processor: persists the completed turn (latest user message
 * + assistant response) to the bound Zep thread in a single
 * `thread.addMessages` call, after the model responds.
 *
 * `processOutputResult` runs exactly once per generation, so the user message
 * is persisted even when the generation ends mid-tool-loop
 * (`finishReason === "tool-calls"`, e.g. the step budget was exhausted) —
 * only the assistant message is omitted when there is no assistant text. The
 * assistant text is the final step's text, never the accumulated
 * all-steps `result.text`.
 *
 * Persistence is fire-and-forget from the caller's perspective — failures are
 * caught internally, logged, and never thrown or aborted.
 */
export class ZepOutputProcessor {
  readonly id = "zep-persist";
  readonly name = "zep-persist";

  private readonly options: ZepOutputProcessorOptions;
  private readonly logger: ZepLogger;

  constructor(options: ZepOutputProcessorOptions) {
    this.options = options;
    this.logger = resolveLogger(options.logger);
  }

  async processOutputResult(args: ProcessOutputResultArgs): Promise<MastraDBMessage[]> {
    // This processor never mutates messages — it only side-effects to Zep —
    // so every branch below returns args.messages unchanged.
    const userText = extractLatestUserText(args.messages);
    const assistantText = extractFinalStepText(args.result);
    if (!userText && !assistantText) {
      return args.messages;
    }

    const identity = await resolveCallIdentity(this.options, args.requestContext);
    if (!identity.threadId) {
      this.logger.warn(
        "[zep-persist] No threadId resolved for this call; skipping persist.",
      );
      return args.messages;
    }

    // Fire-and-forget: never let a Zep failure propagate into the agent loop.
    this.persist(identity.threadId, userText, assistantText).catch((error) => {
      this.logger.warn(`[zep-persist] Failed to persist turn: ${errorMessage(error)}`);
    });

    return args.messages;
  }

  private async persist(
    threadId: string,
    userText: string,
    assistantText: string,
  ): Promise<void> {
    const messages = [];
    if (userText) {
      messages.push({
        role: "user" as const,
        content: truncateForZep(userText, MESSAGE_MAX_CHARS, "zep-persist", this.logger),
      });
    }
    if (assistantText) {
      messages.push({
        role: "assistant" as const,
        content: truncateForZep(assistantText, MESSAGE_MAX_CHARS, "zep-persist", this.logger),
      });
    }

    await this.options.client.thread.addMessages(threadId, { messages });
  }
}

/** Options for {@link createZepProcessors}. */
export interface ZepProcessorsOptions extends ZepProcessorSharedOptions {
  /** Forwarded to {@link ZepInputProcessor}. */
  templateId?: string;
  /** Forwarded to {@link ZepInputProcessor}. */
  contextBuilder?: ZepContextBuilder;
  /** Forwarded to {@link ZepInputProcessor}. */
  contextTemplate?: string;
  /** Forwarded to {@link ZepInputProcessor}. */
  formatContext?: (context: string) => string;
}

/**
 * Build a bound `{ inputProcessor, outputProcessor }` pair for the automatic
 * memory loop:
 *
 * ```ts
 * const { inputProcessor, outputProcessor } = createZepProcessors({
 *   client, userId, threadId,
 * });
 * new Agent({ ..., inputProcessors: [inputProcessor], outputProcessors: [outputProcessor] });
 * ```
 */
export function createZepProcessors(options: ZepProcessorsOptions): {
  inputProcessor: ZepInputProcessor;
  outputProcessor: ZepOutputProcessor;
} {
  const {
    client,
    userId,
    threadId,
    resolveIdentity,
    logger,
    templateId,
    contextBuilder,
    contextTemplate,
    formatContext,
  } = options;

  return {
    inputProcessor: new ZepInputProcessor({
      client,
      userId,
      threadId,
      resolveIdentity,
      logger,
      templateId,
      contextBuilder,
      contextTemplate,
      formatContext,
    }),
    outputProcessor: new ZepOutputProcessor({
      client,
      userId,
      threadId,
      resolveIdentity,
      logger,
    }),
  };
}
