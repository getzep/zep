import type { Zep } from "@getzep/zep-cloud";
import type { ResolvedZepIdentity, ZepBinding, ZepIdentityResolver, ZepLogger } from "./types.js";

/**
 * The Zep `RoleType` enum is closed. Host frameworks often use looser role
 * strings, so we map known aliases and omit unknown roles.
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
 * @returns A valid `RoleType`, or `undefined` for unknown input.
 */
export function toRoleType(role: string | undefined): Zep.RoleType | undefined {
  if (!role) return undefined;
  return ROLE_ALIASES[role.trim().toLowerCase()];
}

/**
 * Resolve a {@link ZepBinding} to the graph UUID used by Zep's V4 API.
 *
 * @returns `{ graphUuid }`, or `null` when no graph is bound.
 */
export function resolveGraphTarget(
  binding: ZepBinding,
): { graphUuid: string } | null {
  if (binding.graphUuid) return { graphUuid: binding.graphUuid };
  return null;
}

/**
 * Normalize an unknown thrown value into a single-line message safe for logs
 * and for returning to a model (never include secrets or PII here).
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
 * Resolve per-call identity for a tool `execute(inputData, context)` call.
 *
 * `resolveIdentity`, when provided, is called with `context?.requestContext`
 * (Mastra's per-call runtime context) and awaited (resolvers may be async);
 * any field it returns overrides the constructor-bound UUID.
 * When `resolveIdentity` is unset, or omits a field, the constructor binding
 * is used unchanged.
 */
export async function resolveToolIdentity(
  binding: ResolvedZepIdentity,
  resolveIdentity: ZepIdentityResolver | undefined,
  context: { requestContext?: unknown } | undefined,
): Promise<ResolvedZepIdentity> {
  const override = await resolveIdentity?.(context?.requestContext);
  return {
    graphUuid: override?.graphUuid ?? binding.graphUuid,
    threadUuid: override?.threadUuid ?? binding.threadUuid,
  };
}

/**
 * Zep rejects messages longer than 4,096 characters with a 400. We truncate a
 * bit below that to leave headroom for any server-side normalization.
 */
export const MESSAGE_MAX_CHARS = 4000;

/**
 * Zep's `graph.episode.add` rejects payloads longer than 10,000 characters
 * with a 400.
 * We truncate to a safety margin under that ceiling (matches the 9900 used by
 * every sibling integration's `GRAPH_MAX_CHARS`).
 */
export const GRAPH_MAX_CHARS = 9900;

/**
 * Truncate `content` to `maxChars` if it exceeds the limit, logging a warning.
 *
 * Zep returns a 400 when a message exceeds 4,096 chars or a
 * `graph.episode.add` payload exceeds 10,000 chars. The integration truncates
 * the content and warns. The warning contains only lengths, not content.
 *
 * @param content - The text about to be sent to Zep.
 * @param maxChars - The hard limit for this Zep operation.
 * @param label - A short tag identifying the call site (e.g. `"zep-remember"`).
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
