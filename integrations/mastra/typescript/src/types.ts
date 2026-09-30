import type { Zep } from "@getzep/zep-cloud";

/**
 * A binding identifies *which* Zep graph a tool reads from and writes to.
 *
 * Zep v4 addresses every graph by its server-generated UUID. A user graph and
 * a standalone graph are therefore the same kind of address:
 *
 * - The UUID of a **user graph** is the `graphUuid` field of the `User` that
 *   `user.create` returns (it is also on every `Thread` of that user). A user
 *   graph is the home for personalized agent memory.
 * - The UUID of a **standalone graph** is the `uuid` field of the `Graph` that
 *   `graph.create` returns. A standalone graph holds shared or domain
 *   knowledge, such as a product knowledge base. It has no user node and no
 *   user summary.
 *
 * Resolve a v3 `userId` or `graphId` name to its UUID one time, then store the
 * UUID in your own database. The integration never calls `lookup`.
 *
 * When no `graphUuid` is bound, a tool gives a graceful message to the model
 * instead of a thrown error.
 */
export interface ZepBinding {
  /** The UUID of the Zep graph that the tools operate on. */
  graphUuid?: string;
}

/**
 * A binding that also identifies the conversation thread.
 *
 * `threadUuid` is the `uuid` field of the `Thread` that `thread.create`
 * returns. The context tools require it, because Zep scopes "what is relevant
 * right now" to the most recent messages of a thread. The thread does not
 * partition memory. Retrieval still spans the whole user graph.
 */
export interface ZepThreadBinding extends ZepBinding {
  /** The UUID of the Zep thread that records conversation and scopes relevance. */
  threadUuid: string;
}

/**
 * Logger interface compatible with `console` and most structured loggers.
 *
 * Tools never throw on a Zep failure; they log a warning through this interface
 * and return a graceful message to the model. Defaults to `console`.
 */
export interface ZepLogger {
  warn: (message: string, ...args: unknown[]) => void;
  debug?: (message: string, ...args: unknown[]) => void;
}

/** Re-export of Zep's closed role enum for convenience. */
export type RoleType = Zep.RoleType;

/** Resolved per-call identity: which Zep graph and thread a call should use. */
export interface ResolvedZepIdentity {
  graphUuid?: string;
  threadUuid?: string;
}

/**
 * A per-call identity override, resolved from a Mastra `requestContext`.
 *
 * May be synchronous or async — a returned promise is awaited before the
 * identity is used. Return `undefined` (or omit a field) to fall back to the
 * constructor-bound identity. Accepted by both {@link "./processors.js"}
 * (per-turn identity) and the tools in the toolset (per-tool-call identity
 * via `execute(inputData, context)`'s `context.requestContext`) — useful when
 * a single processor or tool instance is shared across many end users and
 * identity is only known per request (e.g. from auth middleware that sets
 * values on `RequestContext`).
 */
export type ZepIdentityResolver = (
  requestContext: unknown,
) => ResolvedZepIdentity | undefined | Promise<ResolvedZepIdentity | undefined>;
