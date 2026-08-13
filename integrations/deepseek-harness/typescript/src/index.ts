/**
 * Zep long-term memory plugin for DeepSeek Harness.
 *
 * @packageDocumentation
 */

import type { Context } from "@deepseek-ai/cordis";
import type { PreStepDecision } from "@deepseek-ai/dsh-agent";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import type { ContentBlock, UserMessage } from "@deepseek-ai/dsh-llm";
import type { Session, SessionEvent } from "@deepseek-ai/dsh-session";
import z from "@deepseek-ai/schemastery";
import { Zep, ZepClient } from "@getzep/zep-cloud";

/** Cordis plugin name used in configuration and message provenance. */
export const name = "zep-memory";

/** Services whose events this plugin consumes. */
export const inject = ["agents"];

/** Default wrapper for a Zep Context Block injected into model history. */
export const DEFAULT_CONTEXT_TEMPLATE =
  "Relevant long-term memory from Zep:\n\n{context}";

/** Maximum conversational message size accepted by Zep, with safety headroom. */
export const MESSAGE_MAX_CHARS = 4000;

/** Declarative Zep memory plugin configuration. */
export interface Config {
  /** Zep Cloud API key. Use an environment expression in cordis.yml. */
  apiKey: string;
  /** Stable Zep user identifier shared by this user's sessions. */
  userId: string;
  /**
   * Fixed Zep thread identifier. Omit to derive one from each Harness session
   * id, optionally prefixed by {@link threadIdPrefix}.
   */
  threadId?: string;
  /** Prefix applied when deriving a thread id from the Harness session id. */
  threadIdPrefix?: string;
  /** User first name, used by Zep for identity resolution. */
  firstName?: string;
  /** User last name, used by Zep for identity resolution. */
  lastName?: string;
  /** User email, used by Zep for identity resolution. */
  email?: string;
  /** Display name attached to persisted user messages. */
  userMessageName?: string;
  /** Display name attached to persisted assistant messages. */
  assistantMessageName?: string;
  /** Context Block template containing exactly one `{context}` marker. */
  contextTemplate?: string;
  /** Optional Zep context-template id. */
  contextTemplateId?: string;
  /** Retrieve and inject memory on genuine user turns. */
  recall?: boolean;
  /** Persist completed user/assistant turns. */
  persist?: boolean;
}

/** Runtime validation for declarative plugin configuration. */
export const Config: z<Config> = z.object({
  apiKey: z.string().required(),
  userId: z.string().required(),
  threadId: z.string(),
  threadIdPrefix: z.string(),
  firstName: z.string(),
  lastName: z.string(),
  email: z.string(),
  userMessageName: z.string(),
  assistantMessageName: z.string().default("Assistant"),
  contextTemplate: z.string().default(DEFAULT_CONTEXT_TEMPLATE),
  contextTemplateId: z.string(),
  recall: z.boolean().default(true),
  persist: z.boolean().default(true),
});

/** Minimal logger surface used by the reusable runtime. */
export interface ZepMemoryLogger {
  warn(message: string): void;
  debug?(message: string): void;
}

/** Programmatic runtime options, primarily useful for tests and custom plugins. */
export interface ZepMemoryRuntimeOptions extends Omit<Config, "apiKey"> {
  client: ZepClient;
  logger?: ZepMemoryLogger;
}

function isConflict(error: unknown): boolean {
  if (error instanceof Zep.ConflictError) return true;
  return (
    typeof error === "object" &&
    error !== null &&
    "statusCode" in error &&
    (error as { statusCode?: unknown }).statusCode === 409
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function textFromBlocks(blocks: readonly ContentBlock[]): string {
  return blocks
    .filter((block): block is Extract<ContentBlock, { type: "text" }> => block.type === "text")
    .map((block) => block.text)
    .join("\n")
    .trim();
}

function truncate(content: string, logger: ZepMemoryLogger): string {
  if (content.length <= MESSAGE_MAX_CHARS) return content;
  logger.warn(
    `[zep-memory] Message length ${content.length} exceeds the Zep limit; ` +
      `truncating to ${MESSAGE_MAX_CHARS} characters.`,
  );
  return content.slice(0, MESSAGE_MAX_CHARS);
}

function formatContext(template: string, context: string): string {
  return template.split("{context}").join(context);
}

function validatesTemplate(template: string): void {
  const markers = template.split("{context}").length - 1;
  if (markers !== 1) {
    throw new Error(
      `zep-memory: contextTemplate must contain exactly one "{context}" marker; found ${markers}`,
    );
  }
}

function completedTurn(
  session: Session,
  turn: number,
): { users: string[]; assistant?: string } {
  const start = session.events.findLastIndex(
    (event) => event.type === "turn/start" && event.data.turn === turn,
  );
  if (start < 0) return { users: [] };

  const events = session.events.slice(start + 1);
  const users = events.flatMap((event) => {
    if (event.type !== "user/message" || event.data.source.kind !== "user") return [];
    const text = textFromBlocks(event.data.content);
    return text ? [text] : [];
  });
  const assistantEvent = events.findLast(
    (event) => event.type === "assistant/message" && event.data.turn === turn,
  );
  const assistant =
    assistantEvent?.type === "assistant/message"
      ? textFromBlocks(assistantEvent.data.message.content)
      : undefined;
  return { users, ...(assistant ? { assistant } : {}) };
}

/**
 * Stateful Zep adapter used by the Cordis plugin.
 *
 * The caller owns the supplied client. Every Zep failure is contained and
 * logged without message content, so memory outages do not stop the agent.
 */
export class ZepMemoryRuntime {
  private readonly client: ZepClient;
  private readonly logger: ZepMemoryLogger;
  private readonly options: ZepMemoryRuntimeOptions;
  private userReady = false;
  private userSetup: Promise<boolean> | undefined;
  private readonly readyThreads = new Set<string>();
  private readonly threadSetups = new Map<string, Promise<boolean>>();

  constructor(options: ZepMemoryRuntimeOptions) {
    if (!options.userId.trim()) throw new Error("zep-memory: userId must not be empty");
    if (options.threadId !== undefined && !options.threadId.trim()) {
      throw new Error("zep-memory: threadId must not be empty when provided");
    }
    validatesTemplate(options.contextTemplate ?? DEFAULT_CONTEXT_TEMPLATE);
    this.client = options.client;
    this.logger = options.logger ?? console;
    this.options = options;
  }

  /** Resolve the Zep thread used by one Harness session. */
  threadId(sessionId: string): string {
    return this.options.threadId ?? `${this.options.threadIdPrefix ?? ""}${sessionId}`;
  }

  private async ensureUser(): Promise<boolean> {
    if (this.userReady) return true;
    if (this.userSetup !== undefined) return this.userSetup;
    this.userSetup = (async () => {
      try {
        await this.client.user.add({
          userId: this.options.userId,
          ...(this.options.firstName ? { firstName: this.options.firstName } : {}),
          ...(this.options.lastName ? { lastName: this.options.lastName } : {}),
          ...(this.options.email ? { email: this.options.email } : {}),
        });
        this.userReady = true;
        return true;
      } catch (error) {
        if (isConflict(error)) {
          this.userReady = true;
          return true;
        }
        this.logger.warn(`[zep-memory] Failed to ensure Zep user: ${errorMessage(error)}`);
        return false;
      } finally {
        this.userSetup = undefined;
      }
    })();
    return this.userSetup;
  }

  private async ensureThread(threadId: string): Promise<boolean> {
    if (this.readyThreads.has(threadId)) return true;
    const existing = this.threadSetups.get(threadId);
    if (existing !== undefined) return existing;
    const setup = (async () => {
      if (!(await this.ensureUser())) return false;
      try {
        await this.client.thread.create({ threadId, userId: this.options.userId });
        this.readyThreads.add(threadId);
        return true;
      } catch (error) {
        if (isConflict(error)) {
          this.readyThreads.add(threadId);
          return true;
        }
        this.logger.warn(`[zep-memory] Failed to ensure Zep thread: ${errorMessage(error)}`);
        return false;
      } finally {
        this.threadSetups.delete(threadId);
      }
    })();
    this.threadSetups.set(threadId, setup);
    return setup;
  }

  /** Retrieve a prompt-ready Zep Context Block for a Harness session. */
  async recall(sessionId: string): Promise<string> {
    const threadId = this.threadId(sessionId);
    if (!(await this.ensureThread(threadId))) return "";
    try {
      const response = await this.client.thread.getUserContext(
        threadId,
        this.options.contextTemplateId
          ? { templateId: this.options.contextTemplateId }
          : {},
      );
      const context = response.context?.trim() ?? "";
      return context
        ? formatContext(this.options.contextTemplate ?? DEFAULT_CONTEXT_TEMPLATE, context)
        : "";
    } catch (error) {
      this.logger.warn(`[zep-memory] Failed to retrieve Zep context: ${errorMessage(error)}`);
      return "";
    }
  }

  /** Persist one completed Harness turn to its Zep thread. */
  async persistTurn(session: Session, turn: number): Promise<void> {
    const { users, assistant } = completedTurn(session, turn);
    if (users.length === 0 && assistant === undefined) return;
    const threadId = this.threadId(session.id);
    if (!(await this.ensureThread(threadId))) return;
    const messages: Zep.Message[] = [
      ...users.map(
        (content): Zep.Message => ({
          role: "user",
          content: truncate(content, this.logger),
          ...(this.options.userMessageName
            ? { name: this.options.userMessageName }
            : {}),
        }),
      ),
      ...(assistant
        ? [
            {
              role: "assistant" as const,
              content: truncate(assistant, this.logger),
              name: this.options.assistantMessageName ?? "Assistant",
            },
          ]
        : []),
    ];
    try {
      await this.client.thread.addMessages(threadId, { messages });
    } catch (error) {
      this.logger.warn(`[zep-memory] Failed to persist turn: ${errorMessage(error)}`);
    }
  }
}

function hasGenuineUserMessage(messages: readonly UserMessage[]): boolean {
  return messages.some((message) => message.source.kind === "user");
}

/**
 * Install Zep recall and persistence listeners.
 *
 * Recall runs only on a genuine user turn, then adds a source-attributed
 * `user/message` through the pre-step decision so the Context Block is durable
 * and replayable. Persistence observes successful `turn/end` events and writes
 * the direct user text plus the final assistant text once per turn.
 */
export function apply(ctx: Context, config: Config): void {
  const runtime = new ZepMemoryRuntime({
    ...config,
    client: new ZepClient({ apiKey: config.apiKey }),
    logger: ctx.logger,
  });
  installZepMemory(ctx, runtime, config);
}

/** Handle returned by {@link installZepMemory} for deterministic shutdown/tests. */
export interface ZepMemoryHandle {
  /** Wait for all persistence writes currently in flight. */
  flush(): Promise<void>;
}

/**
 * Install listeners around a caller-supplied runtime.
 *
 * Use this form when the host already owns a shared `ZepClient`; declarative
 * Cordis configuration normally uses {@link apply}.
 */
export function installZepMemory(
  ctx: Context,
  runtime: ZepMemoryRuntime,
  options: Pick<Config, "recall" | "persist"> = {},
): ZepMemoryHandle {
  const jobs = new Set<Promise<void>>();
  const persisted = new WeakMap<Session, Set<number>>();

  const track = (job: Promise<void>): void => {
    jobs.add(job);
    void job.finally(() => jobs.delete(job));
  };

  const flush = async (): Promise<void> => {
    await Promise.allSettled([...jobs]);
  };
  ctx.effect(
    () => flush,
    "zep-memory.pending-writes()",
  );

  if (options.recall ?? true) {
    ctx.on(
      "agent/pre-step",
      async ({ agent, signal }, next): Promise<PreStepDecision> => {
        const decision = await next();
        if (
          decision.kind === "reject" ||
          signal.aborted ||
          !hasGenuineUserMessage(decision.messages)
        ) {
          return decision;
        }
        const context = await runtime.recall(agent.session.id);
        if (!context || signal.aborted) return decision;
        return {
          kind: "enter",
          messages: [
            ...decision.messages,
            createUserMessage({
              content: [{ type: "text", text: context }],
              source: {
                kind: "plugin",
                plugin: name,
                form: "snapshot",
                sections: [{ name, text: context }],
              },
            }),
          ],
        };
      },
      { prepend: true },
    );
  }

  if (options.persist ?? true) {
    ctx.on("session/event", (session: Session, event: SessionEvent) => {
      if (
        event.type !== "turn/end" ||
        !["completed", "max-tokens"].includes(event.data.reason.kind)
      ) {
        return;
      }
      let turns = persisted.get(session);
      if (turns === undefined) {
        turns = new Set();
        persisted.set(session, turns);
      }
      if (turns.has(event.data.turn)) return;
      turns.add(event.data.turn);
      track(runtime.persistTurn(session, event.data.turn));
    });
  }
  return { flush };
}

export default apply;

