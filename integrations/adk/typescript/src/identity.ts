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
 * Set these on the ADK session `state` to attribute persisted messages to
 * the user or to override the UUIDs derived from the ADK session. The user's
 * email on the Zep profile is a provisioning concern — pass it to
 * `createUser`, not session state.
 */
export const STATE_KEYS = {
  /** Overrides the Zep user UUID (defaults to the ADK `userId`). */
  userUuid: "zep_user_uuid",
  /** Overrides the Zep thread UUID (defaults to the ADK `sessionId`). */
  threadUuid: "zep_thread_uuid",
  /** The user's first name — anchors the identity node in the graph. */
  firstName: "zep_first_name",
  /** The user's last name. */
  lastName: "zep_last_name",
} as const;

/**
 * Explicit Zep identity, supplied at construction time.
 *
 * Zep v4 addresses a user and a thread by their server-generated UUIDs. A
 * `userId` or a `threadId` is a name, not an address, so the integration
 * never accepts one here: resolve the UUID once during provisioning (see
 * `createUser` / `createThread`) and store it in your own database.
 *
 * When `userUuid` / `threadUuid` are provided they take precedence over any
 * values resolved from the ADK session at runtime. `firstName` and
 * `lastName` are attached as the author name on persisted messages so the
 * graph resolves identity.
 */
export interface ZepIdentityOptions {
  /** Zep user UUID. Defaults to the ADK session `userId`. */
  userUuid?: string;
  /** Zep thread UUID. Defaults to the ADK session `sessionId`. */
  threadUuid?: string;
  /** User's first name. Recommended — used to anchor the user's graph node. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
}

/** A fully resolved Zep identity for a single turn. */
export interface ResolvedIdentity {
  userUuid: string;
  threadUuid: string;
  firstName?: string;
  lastName?: string;
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
 * - **userUuid**: explicit option → `zep_user_uuid` in state → ADK `userId`
 * - **threadUuid**: explicit option → `zep_thread_uuid` in state → ADK
 *   `sessionId`
 * - **firstName / lastName**: explicit option → matching state key
 *
 * The ADK fallbacks apply only when the application sets the ADK `userId`
 * and `sessionId` to the Zep UUIDs. When it does not, pass the UUIDs
 * explicitly or set the state keys.
 *
 * @param context The ADK callback or tool context for this turn.
 * @param options Explicit identity overrides supplied at construction time.
 * @returns The resolved identity.
 * @throws {ZepIdentityError} If neither an explicit value, a state key, nor
 *   the ADK session can provide a `userUuid` or `threadUuid`.
 */
export function resolveIdentity(
  context: AdkContextLike,
  options: ZepIdentityOptions = {},
): ResolvedIdentity {
  const userUuid =
    options.userUuid ??
    readStateString(context, STATE_KEYS.userUuid) ??
    context.userId;
  if (!userUuid) {
    throw new ZepIdentityError(
      "Cannot resolve a Zep user UUID. Pass `userUuid` to the integration, set " +
        `'${STATE_KEYS.userUuid}' in session state, or create the ADK session with the Zep user UUID as its userId.`,
    );
  }

  const threadUuid =
    options.threadUuid ??
    readStateString(context, STATE_KEYS.threadUuid) ??
    context.sessionId;
  if (!threadUuid) {
    throw new ZepIdentityError(
      "Cannot resolve a Zep thread UUID. Pass `threadUuid` to the integration, set " +
        `'${STATE_KEYS.threadUuid}' in session state, or create the ADK session with the Zep thread UUID as its sessionId.`,
    );
  }

  const firstName =
    options.firstName ?? readStateString(context, STATE_KEYS.firstName);
  const lastName =
    options.lastName ?? readStateString(context, STATE_KEYS.lastName);

  const displayName = [firstName, lastName]
    .filter((part): part is string => Boolean(part))
    .join(" ")
    .trim();

  return {
    userUuid,
    threadUuid,
    firstName,
    lastName,
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
