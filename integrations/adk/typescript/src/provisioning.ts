/**
 * Explicit, out-of-band Zep resource provisioning.
 *
 * The ADK turn path (`persistAndInject`, used by both
 * `createZepBeforeModelCallback` and `ZepContextTool`) never creates Zep
 * users or threads — it only persists messages and retrieves context.
 * Callers are expected to provision the Zep user and thread once,
 * out-of-band, before the first turn (e.g. during account/session
 * onboarding), using {@link ensureUser} and {@link ensureThread}.
 *
 * Both helpers are **create-then-catch-conflict**: they call the Zep SDK's
 * create method directly and treat an "already exists" error as success,
 * rather than checking for existence first (which is racy and costs an
 * extra round-trip). Genuine failures (auth, network, 5xx) always throw —
 * out-of-band provisioning is meant to fail loudly so misconfiguration is
 * caught before the agent ever runs, not swallowed into a silent no-op.
 */

import type { ZepClient } from "@getzep/zep-cloud";

/**
 * Detect whether `error` represents a "resource already exists" conflict.
 *
 * Handles both typed and message-based shapes returned by the Zep SDK:
 *
 * - A 409 status code (`ConflictError`, or any `ApiError`-like object
 *   exposing `statusCode === 409`).
 * - A 400 `BadRequestError` (or similar) whose message mentions "already
 *   exists".
 * - An **untyped** error (no `statusCode`) whose string representation
 *   mentions "already exists" or "conflict" (fallback for untyped/legacy
 *   error shapes).
 *
 * A plain 404 (not found) or any other genuine failure is **not** treated
 * as an already-exists conflict. In particular, a typed error with any
 * other status code (e.g. a 500 whose message happens to mention
 * "conflict") is a genuine failure and must propagate.
 */
function isAlreadyExistsError(error: unknown): boolean {
  const statusCode = (error as { statusCode?: unknown } | null)?.statusCode;
  if (statusCode === 409) {
    return true;
  }

  const text = String(
    error instanceof Error ? error.message : error,
  ).toLowerCase();
  if (statusCode === 400 && text.includes("already exists")) {
    return true;
  }

  // Fallback heuristic for untyped/legacy error shapes only: an error that
  // carries a known non-conflict status code is a genuine failure, no
  // matter what its message says.
  if (statusCode !== undefined) {
    return false;
  }
  return text.includes("already exists") || text.includes("conflict");
}

/**
 * Hook run exactly once, immediately after a Zep user is newly created.
 *
 * Receives the Zep client and the newly created user ID. Use this to
 * configure per-user ontology, custom instructions, or user summary
 * instructions. Awaited before {@link ensureUser} returns; if it throws, the
 * exception propagates to the caller even though the user was successfully
 * created — see {@link EnsureUserOptions.onCreated} for the half-provisioned
 * edge case this implies.
 */
export type UserSetupHook = (
  zep: ZepClient,
  userId: string,
) => Promise<void>;

/** Options accepted by {@link ensureUser}. */
export interface EnsureUserOptions {
  /** The Zep user ID to create. */
  userId: string;
  /** Optional first name, passed through to `zep.user.add`. */
  firstName?: string;
  /** Optional last name, passed through to `zep.user.add`. */
  lastName?: string;
  /** Optional email, passed through to `zep.user.add`. */
  email?: string;
  /**
   * Runs exactly once when the user was newly created; awaited before
   * `ensureUser` returns; errors propagate.
   *
   * **Half-provisioned edge case:** if the user is created but `onCreated`
   * then throws, the user now exists in Zep but its setup did not complete.
   * `ensureUser` will not re-run `onCreated` on a later call (the user
   * already exists, so the create-then-catch-conflict path short-circuits
   * to `false` before `onCreated` would run again). Write `onCreated` to be
   * idempotent (safe to re-run against a user whose setup only partially
   * completed) and give the caller a separate way to retry it if needed.
   */
  onCreated?: UserSetupHook;
}

/**
 * Idempotently ensure the Zep user exists.
 *
 * Calls `zep.user.add(...)` directly (create-then-catch-conflict). If the
 * call fails with an "already exists" conflict, the user is assumed to
 * already be provisioned and the call returns `false` without throwing. Any
 * other failure (auth, network, 5xx) propagates to the caller — this
 * function never swallows genuine errors.
 *
 * When the user is newly created and `onCreated` is provided, the hook is
 * awaited (with `(zep, userId)`) **before** this function returns. If the
 * hook throws, the exception propagates to the caller even though the user
 * was successfully created — see {@link EnsureUserOptions.onCreated} for the
 * half-provisioned edge case, and prefer an idempotent `onCreated`.
 *
 * @param zep An initialised `ZepClient`. The integration never closes it —
 *   the caller owns its lifecycle.
 * @param options User fields and the optional `onCreated` hook.
 * @returns `true` if the user was newly created, `false` if it already
 *   existed.
 * @throws Any genuine failure from the Zep SDK (auth, network, 5xx), or any
 *   error thrown by `onCreated`.
 */
export async function ensureUser(
  zep: ZepClient,
  options: EnsureUserOptions,
): Promise<boolean> {
  const { userId, firstName, lastName, email, onCreated } = options;

  try {
    await zep.user.add({ userId, firstName, lastName, email });
  } catch (error) {
    if (isAlreadyExistsError(error)) {
      return false;
    }
    throw error;
  }

  if (onCreated) {
    await onCreated(zep, userId);
  }

  return true;
}

/** Options accepted by {@link ensureThread}. */
export interface EnsureThreadOptions {
  /** The Zep thread ID to create. */
  threadId: string;
  /** The Zep user ID that owns the thread. The user must already exist (see {@link ensureUser}). */
  userId: string;
}

/**
 * Idempotently ensure the Zep thread exists.
 *
 * Calls `zep.thread.create(...)` directly (create-then-catch-conflict). If
 * the call fails with an "already exists" conflict, the thread is assumed
 * to already be provisioned and the call returns `false` without throwing.
 * Any other failure (auth, network, 5xx) propagates to the caller.
 *
 * @param zep An initialised `ZepClient`. The caller owns its lifecycle.
 * @param options The thread ID to create and the owning user ID.
 * @returns `true` if the thread was newly created, `false` if it already
 *   existed.
 * @throws Any genuine failure from the Zep SDK (auth, network, 5xx).
 */
export async function ensureThread(
  zep: ZepClient,
  options: EnsureThreadOptions,
): Promise<boolean> {
  const { threadId, userId } = options;

  try {
    await zep.thread.create({ threadId, userId });
  } catch (error) {
    if (isAlreadyExistsError(error)) {
      return false;
    }
    throw error;
  }

  return true;
}
