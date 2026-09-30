import type { Zep, ZepClient } from "@getzep/zep-cloud";

/**
 * A binding identifies *which* Zep graph the tools read from and write to.
 *
 * Zep v4 addresses every graph by its server-generated UUID. A user graph and a
 * standalone graph are both a `graphUuid`:
 *
 * - A **user graph** is the home for personalized agent memory. Its UUID is the
 *   `graphUuid` field of the `user.create` (or `user.get`) response. User graphs
 *   carry a user summary and fuse every thread and business record for that user
 *   into one picture.
 * - A **standalone graph** holds shared or domain knowledge (a product knowledge
 *   base, runbooks, and similar). Its UUID is the `uuid` field of the
 *   `graph.create` response. Standalone graphs have no user node and no user
 *   summary.
 *
 * Store the UUID in your own database. If no UUID is supplied, a tool surfaces a
 * graceful result to the model rather than throwing.
 */
export interface ZepBinding {
  /** The UUID of the Zep graph the tools operate on. */
  graphUuid?: string;
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
  /** The UUID of the Zep user for this turn, when configured. */
  userUuid?: string;
  /** The UUID of the Zep thread that scopes this turn. */
  threadUuid: string;
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
 * `thread.getContext` retrieval — useful when you want to assemble context
 * from more than one Zep call, or fold in non-Zep data.
 */
export type ZepContextBuilder = (input: ZepContextBuilderInput) => Promise<string | undefined>;

/**
 * Hook run exactly once, immediately after a Zep user is newly created by
 * {@link CreateIdentityOptions}. Receives the Zep client and the UUID of the new
 * user. Use this to configure per-user ontology, custom instructions, or user
 * summary instructions. Errors are logged, not thrown — a failing hook never
 * changes the identity that {@link createZepUserAndThread} returns.
 */
export type ZepUserCreatedHook = (client: ZepClient, userUuid: string) => void | Promise<void>;
