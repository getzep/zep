import type { Zep, ZepClient } from "@getzep/zep-cloud";

/**
 * A binding identifies *which* Zep graph the tools read from and write to.
 *
 * Exactly one of {@link userId} or {@link graphId} should be supplied:
 *
 * - `userId` targets a **user graph** — the home for personalized agent memory.
 *   This is the right choice for a conversational agent that remembers an end
 *   user across sessions. User graphs carry a user summary and fuse every thread
 *   and business record for that user into one picture.
 * - `graphId` targets a **standalone graph** — shared or domain knowledge (a
 *   product knowledge base, runbooks, etc.). Standalone graphs have no user node
 *   and no user summary.
 *
 * If both are supplied, `userId` wins. If neither is supplied a tool surfaces a
 * graceful result to the model rather than throwing.
 */
export interface ZepBinding {
  /** The Zep user ID whose user graph the tools operate on. */
  userId?: string;
  /** The Zep standalone graph ID the tools operate on. */
  graphId?: string;
}

/**
 * Logger interface compatible with `console` and most structured loggers.
 *
 * Nothing in this package throws on a Zep failure; it logs a warning through
 * this interface and degrades gracefully. Defaults to `console`.
 *
 * **PII rule:** callers and this package only ever log *lengths and counts*
 * through `warn`/`debug` — never message content or user data.
 */
export interface ZepLogger {
  warn: (message: string, ...args: unknown[]) => void;
  debug?: (message: string, ...args: unknown[]) => void;
}

/** Re-export of Zep's closed role enum for convenience. */
export type RoleType = Zep.RoleType;

/**
 * A single conversational turn to persist to Zep: the user's input and the
 * assistant's reply. Either side may be omitted (e.g. persist only the user
 * message up front, or only the assistant message after generation).
 */
export interface ZepTurn {
  /** The user's message content for this turn. */
  user?: string;
  /** The assistant's reply content for this turn. */
  assistant?: string;
  /**
   * Optional speaker name recorded on the user message (e.g. the end user's
   * real name). Passing a real name helps Zep resolve identity in the graph.
   */
  userName?: string;
  /** Optional name recorded on the assistant message. */
  assistantName?: string;
}

/**
 * Input handed to a custom {@link ZepContextBuilder}.
 *
 * Bundling the builder's inputs into a single object (rather than positional
 * arguments) lets us add fields later without breaking existing builders.
 */
export interface ZepContextBuilderInput {
  /** The `ZepClient` in use by the integration. */
  client: ZepClient;
  /** The resolved Zep user ID for this turn, when configured. */
  userId?: string;
  /** The Zep thread that scopes this turn. */
  threadId: string;
  /** The user's message text for this turn. */
  userMessage: string;
  /**
   * The middleware call params for this turn (`transformParams`'s `params`),
   * exposed for power users who need more than `userMessage`. Typed `unknown`
   * to avoid coupling this module to the AI SDK's provider types.
   */
  params?: unknown;
}

/**
 * A custom context builder function.
 *
 * Receives a single {@link ZepContextBuilderInput} and returns the context
 * block to inject into the prompt (or `undefined` to skip injection). Used by
 * {@link ZepMiddlewareOptions.contextBuilder} to replace the default
 * `thread.getUserContext` retrieval — useful when you want to assemble context
 * from more than one Zep call, or fold in non-Zep data.
 */
export type ZepContextBuilder = (input: ZepContextBuilderInput) => Promise<string | undefined>;

/**
 * Hook run exactly once, immediately after a Zep user is newly created by
 * {@link EnsureIdentityOptions}. Receives the Zep client and the newly created
 * user ID. Use this to configure per-user ontology, custom instructions, or
 * user summary instructions. Errors are logged, not thrown — a failing hook
 * never flips {@link ensureZepUserAndThread}'s `Promise<boolean>` "ready"
 * result to `false`.
 */
export type ZepUserCreatedHook = (client: ZepClient, userId: string) => void | Promise<void>;
