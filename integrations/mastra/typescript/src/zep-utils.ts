import type { Zep } from "@getzep/zep-cloud";
import type { ZepBinding, ZepLogger } from "./types.js";

/**
 * The Zep `RoleType` enum is closed (`user | assistant | system | tool |
 * function | norole`). Host frameworks often use looser role strings, so we map
 * common aliases onto the valid set and fall back to `norole` for anything we
 * don't recognize (rather than letting an invalid role reach the API).
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
  norole: "norole",
};

/**
 * Coerce an arbitrary role string into a valid Zep {@link Zep.RoleType}.
 *
 * @param role - A role string from the host framework (case-insensitive).
 * @returns A valid `RoleType`; defaults to `"norole"` for unknown input.
 */
export function toRoleType(role: string | undefined): Zep.RoleType {
  if (!role) return "norole";
  return ROLE_ALIASES[role.trim().toLowerCase()] ?? "norole";
}

/**
 * Resolve a {@link ZepBinding} into the mutually-exclusive `userId`/`graphId`
 * pair that Zep's `graph.add` / `graph.search` accept.
 *
 * `userId` takes precedence over `graphId` when both are set, because a user
 * graph is the richer target (it carries identity and a user summary).
 *
 * @returns `{ userId }`, `{ graphId }`, or `null` when neither is bound.
 */
export function resolveGraphTarget(
  binding: ZepBinding,
): { userId: string } | { graphId: string } | null {
  if (binding.userId) return { userId: binding.userId };
  if (binding.graphId) return { graphId: binding.graphId };
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
