/**
 * Explicit, out-of-band Zep resource provisioning.
 *
 * The ADK turn path (`persistAndInject`, used by both
 * `createZepBeforeModelCallback` and `ZepContextTool`) never creates Zep
 * users or threads — it only persists messages and retrieves context.
 * Callers are expected to provision the Zep user and thread once,
 * out-of-band, before the first turn (e.g. during account/session
 * onboarding), using {@link createUser} and {@link createThread}.
 *
 * Zep v4 assigns the UUID of a user, of its graph, and of a thread on the
 * server, so a create call is not idempotent and there is no "already
 * exists" conflict to absorb. Each helper returns the UUIDs of the new
 * resource. Store them in your own database and pass them back to the
 * integration as `userUuid` / `threadUuid`. Every failure (auth, network,
 * 5xx) throws — out-of-band provisioning is meant to fail loudly so
 * misconfiguration is caught before the agent ever runs.
 */

import type { ZepClient } from "@getzep/zep-cloud";

/**
 * Hook run exactly once, immediately after a Zep user is created.
 *
 * Receives the Zep client and the UUID of the new user. Use this to
 * configure per-user ontology, custom instructions, or user summary
 * instructions. Awaited before {@link createUser} returns; if it throws, the
 * exception propagates to the caller even though the user was successfully
 * created — see {@link CreateUserOptions.onCreated} for the half-provisioned
 * edge case this implies.
 */
export type UserSetupHook = (
  zep: ZepClient,
  userUuid: string,
) => Promise<void>;

/** Options accepted by {@link createUser}. */
export interface CreateUserOptions {
  /**
   * Optional developer-assigned name for the user. It is a label only: the
   * address of the user is the UUID that Zep returns.
   */
  userId?: string;
  /** Optional first name, passed through to `zep.user.create`. */
  firstName?: string;
  /** Optional last name, passed through to `zep.user.create`. */
  lastName?: string;
  /** Optional email, passed through to `zep.user.create`. */
  email?: string;
  /**
   * Runs exactly once after the user is created; awaited before
   * `createUser` returns; errors propagate.
   *
   * **Half-provisioned edge case:** if the user is created but `onCreated`
   * then throws, the user now exists in Zep but its setup did not complete.
   * Write `onCreated` to be idempotent (safe to re-run against a user whose
   * setup only partially completed) and give the caller a separate way to
   * retry it if needed.
   */
  onCreated?: UserSetupHook;
}

/** The UUIDs that Zep assigns to a new user. */
export interface CreatedUser {
  /** The UUID of the user. This is the v4 address of the user. */
  userUuid: string;
  /** The UUID of the graph of the user, used for graph search. */
  graphUuid?: string;
}

/**
 * Create a Zep user and return its server-generated UUIDs.
 *
 * When `onCreated` is provided, the hook is awaited (with
 * `(zep, userUuid)`) **before** this function returns. If the hook throws,
 * the exception propagates to the caller even though the user was
 * successfully created — see {@link CreateUserOptions.onCreated}.
 *
 * @param zep An initialised `ZepClient`. The integration never closes it —
 *   the caller owns its lifecycle.
 * @param options User fields and the optional `onCreated` hook.
 * @returns The UUID of the user and the UUID of its graph.
 * @throws Any failure from the Zep SDK (auth, network, 5xx), any error
 *   thrown by `onCreated`, or an error when the response carries no UUID.
 */
export async function createUser(
  zep: ZepClient,
  options: CreateUserOptions = {},
): Promise<CreatedUser> {
  const { userId, firstName, lastName, email, onCreated } = options;

  const user = await zep.user.create({ userId, firstName, lastName, email });
  if (!user.uuid) {
    throw new Error("Zep did not return a UUID for the new user.");
  }

  if (onCreated) {
    await onCreated(zep, user.uuid);
  }

  return { userUuid: user.uuid, graphUuid: user.graphUuid };
}

/** Options accepted by {@link createThread}. */
export interface CreateThreadOptions {
  /** The UUID of the Zep user that owns the thread. */
  userUuid: string;
  /**
   * Optional developer-assigned name for the thread. It is a label only:
   * the address of the thread is the UUID that Zep returns.
   */
  threadId?: string;
}

/** The UUIDs that Zep assigns to a new thread. */
export interface CreatedThread {
  /** The UUID of the thread. This is the v4 address of the thread. */
  threadUuid: string;
  /** The UUID of the graph that the thread writes to. */
  graphUuid?: string;
}

/**
 * Create a Zep thread for a user and return its server-generated UUID.
 *
 * @param zep An initialised `ZepClient`. The caller owns its lifecycle.
 * @param options The UUID of the owning user and an optional thread name.
 * @returns The UUID of the thread and the UUID of the graph it writes to.
 * @throws Any failure from the Zep SDK (auth, network, 5xx), or an error
 *   when the response carries no UUID.
 */
export async function createThread(
  zep: ZepClient,
  options: CreateThreadOptions,
): Promise<CreatedThread> {
  const { userUuid, threadId } = options;

  const thread = await zep.thread.create({ userUuid, threadId });
  if (!thread.uuid) {
    throw new Error("Zep did not return a UUID for the new thread.");
  }

  return { threadUuid: thread.uuid, graphUuid: thread.graphUuid };
}
