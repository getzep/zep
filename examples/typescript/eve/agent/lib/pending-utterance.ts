/**
 * Bridge current-turn user text from channel `onMessage` into
 * `turn.started` dynamic instructions.
 *
 * Eve emits `turn.started` (where dynamic instructions resolve) before
 * `message.received`, and the turn.started resolver does not receive the
 * inbound utterance. Channel `onMessage` runs earlier — after the HTTP
 * message is parsed — so we stash the text there and consume it on
 * `turn.started`.
 *
 * Create-session calls `onMessage` without a `sessionId`, so the first
 * utterance is queued by `userId` until `session.started` rebinds it to
 * the new session id (FIFO if several sessions share a demo user).
 *
 * In-process only — requires `onMessage` and `turn.started` in the same
 * Node process (`eve dev` / single instance). Not safe across Vercel
 * Workflow isolates without an external store.
 */

const TTL_MS = 60_000;

type Entry = { text: string; expiresAt: number };

const bySession = new Map<string, Entry>();
const byUser = new Map<string, Entry[]>();

function pruneSession(now: number): void {
  for (const [key, entry] of bySession) {
    if (entry.expiresAt <= now) bySession.delete(key);
  }
}

function pruneUserQueues(now: number): void {
  for (const [key, queue] of byUser) {
    const next = queue.filter((e) => e.expiresAt > now);
    if (next.length === 0) byUser.delete(key);
    else byUser.set(key, next);
  }
}

function prune(now: number): void {
  pruneSession(now);
  pruneUserQueues(now);
}

/** Record the inbound user utterance for the upcoming turn. */
export function stashPendingUtterance(options: {
  text: string;
  sessionId?: string | null;
  userId?: string | null;
}): void {
  const text = options.text.trim();
  if (!text) return;

  const now = Date.now();
  prune(now);
  const entry: Entry = { text, expiresAt: now + TTL_MS };

  // Follow-ups: key by session only (precise).
  if (options.sessionId) {
    bySession.set(options.sessionId, entry);
    return;
  }

  // Create-session: no session id yet — FIFO queue per user.
  if (!options.userId) return;
  const queue = byUser.get(options.userId) ?? [];
  queue.push(entry);
  byUser.set(options.userId, queue);
}

/**
 * After Eve mints a session id, move one queued user utterance onto that
 * session. Safe to call repeatedly; no-ops if the session already has a
 * stash or the user queue is empty. Refreshes TTL so slow session.started
 * work (ensure user/thread) cannot expire the entry before turn.started peeks.
 */
export function bindPendingUtteranceToSession(options: {
  sessionId: string;
  userId: string;
}): void {
  const now = Date.now();
  prune(now);
  if (bySession.has(options.sessionId)) {
    const existing = bySession.get(options.sessionId)!;
    bySession.set(options.sessionId, {
      text: existing.text,
      expiresAt: now + TTL_MS,
    });
    return;
  }

  const queue = byUser.get(options.userId);
  if (!queue || queue.length === 0) return;

  const entry = queue.shift()!;
  if (queue.length === 0) byUser.delete(options.userId);
  else byUser.set(options.userId, queue);

  bySession.set(options.sessionId, {
    text: entry.text,
    expiresAt: now + TTL_MS,
  });
}

/**
 * Read the stashed utterance without removing it (so a failed search can
 * retry within the same turn.started resolver). Prefer session key, then
 * the head of the user queue. Extends TTL on hit so a long search is not
 * pruned by concurrent stash activity.
 */
export function peekPendingUtterance(options: {
  sessionId: string;
  userId: string;
}): { text: string; source: "session" | "user" } | undefined {
  const now = Date.now();
  prune(now);

  const fromSession = bySession.get(options.sessionId);
  if (fromSession) {
    bySession.set(options.sessionId, {
      text: fromSession.text,
      expiresAt: now + TTL_MS,
    });
    return { text: fromSession.text, source: "session" };
  }

  const queue = byUser.get(options.userId);
  const head = queue?.[0];
  if (!head) return undefined;
  queue[0] = { text: head.text, expiresAt: now + TTL_MS };
  byUser.set(options.userId, queue);
  return { text: head.text, source: "user" };
}

/** Drop the stashed utterance after recall has finished (success or empty). */
export function clearPendingUtterance(options: {
  sessionId: string;
  userId: string;
  source: "session" | "user";
}): void {
  if (options.source === "session") {
    bySession.delete(options.sessionId);
    return;
  }
  const queue = byUser.get(options.userId);
  if (!queue || queue.length === 0) return;
  queue.shift();
  if (queue.length === 0) byUser.delete(options.userId);
  else byUser.set(options.userId, queue);
}
