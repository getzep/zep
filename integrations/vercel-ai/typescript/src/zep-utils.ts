import type { Zep } from "@getzep/zep-cloud";
import type { ZepBinding, ZepLogger } from "./types.js";

/**
 * The Zep `RoleType` enum is closed (`user | assistant | system | tool |
 * function`). Host frameworks often use looser role strings, so we map common
 * aliases onto the valid set. Zep v4 removed the `norole` member, so an
 * unrecognized role maps to `undefined` and the caller omits the role rather
 * than letting an invalid role reach the API.
 */
const ROLE_ALIASES: Record<string, Zep.RoleType> = {
  user: "user",
  human: "user",
  assistant: "assistant",
  ai: "assistant",
  bot: "assistant",
  model: "assistant",
  system: "system",
  developer: "system",
  tool: "tool",
  function: "function",
};

/**
 * Coerce an arbitrary role string into a valid Zep {@link Zep.RoleType}.
 *
 * @param role - A role string from the host framework (case-insensitive).
 * @param logger - Optional logger; when supplied, an unknown role is logged at
 *   debug with the role **name only** (never content).
 * @returns A valid `RoleType`, or `undefined` for unknown or absent input.
 */
export function toRoleType(
  role: string | undefined,
  logger?: ZepLogger,
): Zep.RoleType | undefined {
  if (!role) return undefined;
  const mapped = ROLE_ALIASES[role.trim().toLowerCase()];
  if (mapped === undefined) {
    // Role name only — no content/PII.
    logger?.debug?.(`[zep] Unknown role '${role}' has no Zep role; the role is omitted.`);
    return undefined;
  }
  return mapped;
}

/**
 * Resolve a {@link ZepBinding} into the graph UUID that Zep's
 * `graph.episode.add` and `graph.search*` methods take as their first argument.
 *
 * The UUID identifies a user graph or a standalone graph. Zep v4 addresses both
 * graph types the same way.
 *
 * @returns The bound graph UUID, or `null` when no graph is bound.
 */
export function resolveGraphTarget(binding: ZepBinding): string | null {
  return binding.graphUuid || null;
}

/**
 * Normalize an unknown thrown value into a single-line message safe for logs.
 *
 * Never include secrets or PII here — this is intended for error *types* and
 * messages emitted by the Zep SDK, not user content.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return "unknown error";
  }
}

/** Resolve the effective logger, defaulting to `console`. */
export function resolveLogger(logger: ZepLogger | undefined): ZepLogger {
  return logger ?? console;
}

/**
 * Zep rejects messages longer than 4,096 characters with a 400. We truncate a
 * bit below that to leave headroom for any server-side normalization. This
 * applies to the conversational `thread.addMessages` path.
 */
export const MESSAGE_MAX_CHARS = 4000;

/**
 * Zep's `graph.episode.add` endpoint rejects `data` longer than 10,000
 * characters with a 400. We truncate a bit below that to leave headroom. This
 * applies to the non-conversational episode path (larger documents/business
 * data than the conversational message limit).
 */
export const GRAPH_MAX_CHARS = 9900;

/**
 * Truncate `content` to `maxChars` if it exceeds the limit, logging a warning.
 *
 * Zep returns a 400 when a message exceeds its 4,096-char limit. Rather than
 * letting the call fail (or silently dropping data), we truncate to the limit
 * and warn. The warning contains **only lengths** (never the content itself) to
 * avoid leaking PII into logs — a hard rule across this package.
 *
 * @param content - The text about to be sent to Zep.
 * @param maxChars - The hard limit for this Zep operation.
 * @param label - A short tag identifying the call site (e.g. `"zep-middleware"`).
 * @param logger - Where to emit the truncation warning.
 * @returns The original string, or a truncated copy when it was too long.
 */
export function truncateForZep(
  content: string,
  maxChars: number,
  label: string,
  logger: ZepLogger,
): string {
  if (content.length <= maxChars) return content;
  logger.warn(
    `[${label}] Content length ${content.length} exceeds Zep limit ${maxChars}; ` +
      `truncating to ${maxChars} characters.`,
  );
  return content.slice(0, maxChars);
}
