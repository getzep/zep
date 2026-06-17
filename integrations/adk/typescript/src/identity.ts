/**
 * Shared types and identity resolution for the Zep ADK integration.
 *
 * The ADK `Context` (passed to `beforeModelCallback`) and the tool `Context`
 * (passed to `BaseTool.processLlmRequest` / `runAsync`) both expose `userId`,
 * `sessionId`, `userContent`, and a delta-aware session `state`. This module
 * resolves a Zep identity from those, with optional explicit overrides.
 */

import type { Content } from "@google/genai";
import { ZepIdentityError } from "./errors.js";

/**
 * Session-state keys read when resolving a Zep identity at runtime.
 *
 * Set these on the ADK session `state` to enrich the Zep user profile or to
 * override the IDs derived from the ADK session.
 */
export const STATE_KEYS = {
  /** Overrides the Zep user ID (defaults to the ADK `userId`). */
  userId: "zep_user_id",
  /** Overrides the Zep thread ID (defaults to the ADK `sessionId`). */
  threadId: "zep_thread_id",
  /** The user's first name — anchors the identity node in the graph. */
  firstName: "zep_first_name",
  /** The user's last name. */
  lastName: "zep_last_name",
  /** The user's email address. */
  email: "zep_email",
} as const;

/**
 * Explicit Zep identity, supplied at construction time.
 *
 * When `userId` / `threadId` are provided they take precedence over any
 * values resolved from the ADK session at runtime. `firstName`, `lastName`,
 * and `email` enrich the Zep user profile so the graph resolves identity.
 */
export interface ZepIdentityOptions {
  /** Zep user ID. Defaults to the ADK session `userId`. */
  userId?: string;
  /** Zep thread ID. Defaults to the ADK session `sessionId`. */
  threadId?: string;
  /** User's first name. Recommended — used to anchor the user's graph node. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
  /** User's email address. */
  email?: string;
}

/** A fully resolved Zep identity for a single turn. */
export interface ResolvedIdentity {
  userId: string;
  threadId: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  /** Display name used as the `name` on persisted user messages. */
  displayName?: string;
}

/**
 * Minimal structural view of the ADK `Context` / tool `Context` objects.
 *
 * Both `beforeModelCallback`'s `context` and `BaseTool`'s `toolContext` are
 * instances of ADK's `Context` class. We depend only on the fields we read,
 * which keeps the integration resilient to unrelated ADK changes.
 */
export interface AdkContextLike {
  readonly userId: string;
  readonly sessionId: string;
  /**
   * The current ADK invocation id. Stable across the multiple
   * `beforeModelCallback` / `processLlmRequest` firings of a single
   * (possibly tool-using) turn, so it keys the same-turn dedup guard.
   */
  readonly invocationId: string;
  readonly userContent?: Content;
  readonly state: { get<T>(key: string, defaultValue?: T): T | undefined };
}

function readStateString(
  context: AdkContextLike,
  key: string,
): string | undefined {
  try {
    const value = context.state.get<unknown>(key);
    return typeof value === "string" && value.length > 0 ? value : undefined;
  } catch {
    // Some ADK contexts expose state lazily; treat read failures as "unset".
    return undefined;
  }
}

/**
 * Resolve a concrete Zep identity for the current turn.
 *
 * Resolution order for each field:
 *
 * - **userId**: explicit option → `zep_user_id` in state → ADK `userId`
 * - **threadId**: explicit option → `zep_thread_id` in state → ADK `sessionId`
 * - **firstName / lastName / email**: explicit option → matching state key
 *
 * @param context The ADK callback or tool context for this turn.
 * @param options Explicit identity overrides supplied at construction time.
 * @returns The resolved identity.
 * @throws {ZepIdentityError} If neither an explicit value, a state key, nor
 *   the ADK session can provide a `userId` or `threadId`.
 */
export function resolveIdentity(
  context: AdkContextLike,
  options: ZepIdentityOptions = {},
): ResolvedIdentity {
  const userId =
    options.userId ?? readStateString(context, STATE_KEYS.userId) ?? context.userId;
  if (!userId) {
    throw new ZepIdentityError(
      "Cannot resolve a Zep user ID. Pass `userId` to the integration, set " +
        `'${STATE_KEYS.userId}' in session state, or create the ADK session with a userId.`,
    );
  }

  const threadId =
    options.threadId ??
    readStateString(context, STATE_KEYS.threadId) ??
    context.sessionId;
  if (!threadId) {
    throw new ZepIdentityError(
      "Cannot resolve a Zep thread ID. Pass `threadId` to the integration, set " +
        `'${STATE_KEYS.threadId}' in session state, or create the ADK session with a sessionId.`,
    );
  }

  const firstName =
    options.firstName ?? readStateString(context, STATE_KEYS.firstName);
  const lastName =
    options.lastName ?? readStateString(context, STATE_KEYS.lastName);
  const email = options.email ?? readStateString(context, STATE_KEYS.email);

  const displayName = [firstName, lastName]
    .filter((part): part is string => Boolean(part))
    .join(" ")
    .trim();

  return {
    userId,
    threadId,
    firstName,
    lastName,
    email,
    displayName: displayName.length > 0 ? displayName : undefined,
  };
}

/**
 * Extract and join the text parts of an ADK `Content` value.
 *
 * `Content.parts` may interleave text with non-text parts (images, files,
 * function calls). Only text parts are returned, joined by spaces.
 *
 * @param content The ADK content to read, or `undefined`.
 * @returns The concatenated text, or `undefined` when there is no text.
 */
export function extractText(content: Content | undefined): string | undefined {
  const parts = content?.parts;
  if (!parts || parts.length === 0) {
    return undefined;
  }
  const text = parts
    .map((part) => part.text)
    .filter((value): value is string => Boolean(value))
    .join(" ")
    .trim();
  return text.length > 0 ? text : undefined;
}
