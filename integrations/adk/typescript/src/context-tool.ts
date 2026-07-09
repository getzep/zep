/**
 * `ZepContextTool` — a tool-centric alternative to
 * `createZepBeforeModelCallback`.
 *
 * It subclasses ADK's `BaseTool` and overrides `processLlmRequest`, the hook
 * ADK's own `PreloadMemoryTool` uses. The tool is never *called* by the model
 * (`_getDeclaration` returns `undefined`, `runAsync` is a no-op); instead it
 * preprocesses each outgoing LLM request to persist the user message and inject
 * the Zep Context Block.
 *
 * Use this when you prefer to compose memory as a tool in `LlmAgent.tools`
 * rather than wiring `beforeModelCallback`. Pick one or the other — running
 * both would persist each user message twice.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import {
  BaseTool,
  type Context,
  type LlmRequest,
} from "@google/adk";
import type { FunctionDeclaration } from "@google/genai";
import type { ZepIdentityOptions } from "./identity.js";
import { persistAndInject, type ContextBuilder } from "./inject.js";
import { defaultLogger, type Logger } from "./logging.js";
import { TurnDedup } from "./resources.js";

/** Options for the {@link ZepContextTool} constructor. */
export interface ZepContextToolOptions extends ZepIdentityOptions {
  /** An initialised `ZepClient`. The caller owns its lifecycle. */
  zep: ZepClient;
  /**
   * Roles to exclude from Zep's knowledge-graph ingestion. Messages are still
   * stored in the thread and used to contextualize other messages.
   */
  ignoreRoles?: Zep.RoleType[];
  /** Tool name. Defaults to `"zep_context"`. */
  name?: string;
  /** Tool description. Defaults to a short summary. */
  description?: string;
  /** Logger for Zep failures. Defaults to a `console`-backed logger. */
  logger?: Logger;
  /**
   * An optional async function that builds the context block to inject,
   * instead of the default `thread.addMessages(returnContext: true)`
   * round-trip. When set, persistence and the builder run concurrently. See
   * `persistAndInject` in `src/inject.ts` for the full error-isolation
   * contract.
   */
  contextBuilder?: ContextBuilder;
  /**
   * Template used to wrap retrieved context before injecting it into the
   * LLM's system instructions. Must contain a literal `{context}`
   * placeholder. Defaults to `DEFAULT_CONTEXT_TEMPLATE`.
   */
  contextTemplate?: string;
}

/**
 * An ADK `BaseTool` that injects Zep long-term memory into every LLM request.
 *
 * @example
 * ```ts
 * import { LlmAgent } from "@google/adk";
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { ZepContextTool } from "@getzep/zep-adk";
 *
 * const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const agent = new LlmAgent({
 *   name: "memory_agent",
 *   model: "gemini-2.5-flash",
 *   instruction: "You are a helpful assistant with long-term memory.",
 *   tools: [new ZepContextTool({ zep, userId: "user-123", threadId: "thread-abc" })],
 * });
 * ```
 */
export class ZepContextTool extends BaseTool {
  private readonly zep: ZepClient;
  private readonly logger: Logger;
  private readonly dedup: TurnDedup;
  private readonly ignoreRoles?: Zep.RoleType[];
  private readonly identity: ZepIdentityOptions;
  private readonly contextBuilder?: ContextBuilder;
  private readonly contextTemplate?: string;

  constructor(options: ZepContextToolOptions) {
    super({
      name: options.name ?? "zep_context",
      description:
        options.description ??
        "Injects relevant long-term memory about the user from Zep into the prompt.",
    });
    this.zep = options.zep;
    this.logger = options.logger ?? defaultLogger;
    this.dedup = new TurnDedup();
    this.ignoreRoles = options.ignoreRoles;
    this.identity = {
      userId: options.userId,
      threadId: options.threadId,
      firstName: options.firstName,
      lastName: options.lastName,
    };
    this.contextBuilder = options.contextBuilder;
    this.contextTemplate = options.contextTemplate;
  }

  /**
   * The tool is not model-callable, so it exposes no function declaration.
   *
   * Returning `undefined` keeps it out of the model's tool list while still
   * letting ADK invoke {@link ZepContextTool.processLlmRequest}.
   */
  override _getDeclaration(): FunctionDeclaration | undefined {
    return undefined;
  }

  /**
   * No-op: the tool is never invoked by the model. All work happens in
   * {@link ZepContextTool.processLlmRequest}.
   */
  override async runAsync(): Promise<unknown> {
    return undefined;
  }

  /**
   * Persist the latest user message to Zep and inject the Context Block into
   * the outgoing request's system instruction.
   *
   * Called by ADK before each LLM request. Never throws on a Zep error.
   */
  override async processLlmRequest({
    toolContext,
    llmRequest,
  }: {
    toolContext: Context;
    llmRequest: LlmRequest;
  }): Promise<void> {
    await persistAndInject({
      zep: this.zep,
      dedup: this.dedup,
      logger: this.logger,
      context: toolContext,
      llmRequest,
      options: {
        ...this.identity,
        ignoreRoles: this.ignoreRoles,
        contextBuilder: this.contextBuilder,
        contextTemplate: this.contextTemplate,
      },
    });
  }
}
