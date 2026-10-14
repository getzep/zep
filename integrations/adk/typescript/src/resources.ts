/**
 * Same-turn dedup guard shared by the before- and after-model callbacks (and
 * the tools).
 *
 * This module is internal: it no longer creates Zep users or threads (see
 * `src/provisioning.ts` for the explicit, out-of-band `createUser` /
 * `createThread` helpers callers are expected to use before the first turn).
 * All that remains here is the per-invocation dedup cache that prevents a
 * tool-using turn — which fires the before-model hook multiple times with
 * the same `invocationId` — from persisting the same user message more than
 * once.
 */

/**
 * Holds the per-invocation dedup state for user-message persistence.
 *
 * A single instance is shared by the before- and after-model callbacks (and
 * the tools) so the dedup guard is not split-brain across hooks that fire
 * within the same turn.
 */
export class TurnDedup {
  /**
   * Same-turn dedup guard. Maps a thread UUID to the `invocationId` of the last
   * user turn whose message we successfully persisted to that thread.
   *
   * Within one ADK turn the before-model hook fires multiple times (tool-use
   * loops) with the *same* `invocationId`. Comparing it lets us persist the
   * user message once per invocation without blocking a legitimately repeated
   * message in a later turn (which carries a new `invocationId`).
   */
  private readonly lastPersistedInvocation = new Map<string, string>();

  /**
   * Whether the user message for `invocationId` has already been persisted to
   * `threadUuid` in the current turn.
   *
   * @returns `true` if this (thread, invocation) pair was already persisted and
   *   the caller should skip re-persisting; `false` otherwise.
   */
  alreadyPersisted(threadUuid: string, invocationId: string): boolean {
    return this.lastPersistedInvocation.get(threadUuid) === invocationId;
  }

  /**
   * Record that the user message for `invocationId` was persisted to
   * `threadUuid`. Call this only AFTER `addMessages` succeeds, so a transient
   * failure never permanently suppresses the message.
   */
  markPersisted(threadUuid: string, invocationId: string): void {
    this.lastPersistedInvocation.set(threadUuid, invocationId);
  }
}
