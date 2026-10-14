import type { Zep } from "@getzep/zep-cloud";
import type { ZepBinding, ZepLogger } from "./types.js";

/**
 * The Zep `RoleType` enum is closed. Host frameworks often use looser role
 * strings, so this mapper normalizes common aliases and omits unknown roles.
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
 *   debug with the role name only.
 * @returns A valid `RoleType`, or `undefined` for an unknown or empty role.
 */
export function toRoleType(
  role: string | undefined,
  logger?: ZepLogger,
): Zep.RoleType | undefined {
  if (!role) return undefined;
  const mapped = ROLE_ALIASES[role.trim().toLowerCase()];
  if (mapped === undefined) {
    logger?.debug?.(`[zep] Unknown role '${role}' omitted.`);
    return undefined;
  }
  return mapped;
}

/**
 * Resolve a {@link ZepBinding} to its graph UUID.
 *
 * @returns The graph UUID, or `null` when no graph is bound.
 */
export function resolveGraphTarget(
  binding: ZepBinding,
): string | null {
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
 * Zep rejects messages longer than 4,096 characters with a 400. We truncate
 * to 4,000 characters to leave headroom for server-side normalization. This
 * applies to the conversational `thread.addMessages` path.
 */
export const MESSAGE_MAX_CHARS = 4000;

/**
 * Zep rejects episode data longer than 10,000 characters. We truncate below
 * that limit to leave headroom. This applies to the non-conversational
 * `graph.episode.add` path.
 */
export const GRAPH_MAX_CHARS = 9900;

/**
 * Truncate `content` to `maxChars` if it exceeds the limit, logging a warning.
 *
 * Zep returns a 400 when a message exceeds its 4,096-char limit. We truncate
 * to the configured limit and warn. The warning contains **only lengths**
 * (never the content itself) to
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
    `[${label}] Content length ${content.length} exceeds the package limit ${maxChars}; ` +
      `truncating to ${maxChars} characters.`,
  );
  return content.slice(0, maxChars);
}
