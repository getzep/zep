import type { Zep } from "@getzep/zep-cloud";

/**
 * A binding identifies *which* Zep graph a tool reads from and writes to.
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
 * graceful error message to the model rather than throwing.
 */
export interface ZepBinding {
  /** The Zep user ID whose user graph the tools operate on. */
  userId?: string;
  /** The Zep standalone graph ID the tools operate on. */
  graphId?: string;
}

/**
 * A binding that also identifies the conversation thread.
 *
 * `threadId` is required by the context tools ({@link ZepBinding} alone is not
 * enough) because Zep scopes "what is relevant right now" to a thread's most
 * recent messages. The thread does not partition memory — retrieval still spans
 * the whole user graph — it only focuses relevance.
 */
export interface ZepThreadBinding extends ZepBinding {
  /** The Zep thread ID used to scope relevance and record conversation history. */
  threadId: string;
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
