import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { ZepIdentityResolver, ZepThreadBinding, ZepLogger } from "./types.js";
import { createZepRememberTool } from "./remember-tool.js";
import { createZepSearchTool } from "./search-tool.js";
import { createZepContextTool } from "./context-tool.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";

/** Options for {@link createZepToolset}. */
export interface ZepToolsetOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /**
   * Identity + thread binding for the tools. For a personalized conversational
   * agent, supply `userId` and `threadId`; for a shared knowledge base, supply
   * `graphId` (the context tool still requires a `threadId`).
   */
  binding: ZepThreadBinding;
  /** Pin the search scope (default `"edges"`). */
  searchScope?: Zep.GraphSearchScope;
  /** Pin the search result limit. */
  searchLimit?: number;
  /** Default speaker name recorded on conversational messages persisted by `zep-remember`. */
  defaultMessageName?: string;
  /**
   * Resolve identity per tool call from the tool's `requestContext`,
   * overriding the constructor-bound `binding`. Forwarded to all three tools.
   */
  resolveIdentity?: ZepIdentityResolver;
  /** Logger for Zep failures across all tools. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * The Zep tool set, keyed for direct use as an Agent `tools` record:
 *
 * ```ts
 * const { zepRemember, zepSearch, zepContext } = createZepToolset({ client, binding });
 * new Agent({ id, name, instructions, model, tools: { zepRemember, zepSearch, zepContext } });
 * ```
 */
export interface ZepToolset {
  /** Persist a message or fact to Zep. */
  zepRemember: ReturnType<typeof createZepRememberTool>;
  /** Search the bound graph for relevant facts. */
  zepSearch: ReturnType<typeof createZepSearchTool>;
  /** Retrieve the whole-user-graph Context Block. */
  zepContext: ReturnType<typeof createZepContextTool>;
}

/**
 * Build the full set of Zep tools bound to a single client and binding.
 *
 * This is the recommended entry point: spread the returned object into an
 * Agent's `tools` record. Each tool handles Zep failures gracefully and never
 * throws.
 */
export function createZepToolset(options: ZepToolsetOptions): ZepToolset {
  const { client, binding, resolveIdentity } = options;
  const logger = resolveLogger(options.logger);
  const identityOption =
    resolveIdentity !== undefined ? { resolveIdentity } : {};

  return {
    zepRemember: createZepRememberTool({
      client,
      binding,
      ...(options.defaultMessageName !== undefined
        ? { defaultMessageName: options.defaultMessageName }
        : {}),
      ...identityOption,
      logger,
    }),
    zepSearch: createZepSearchTool({
      client,
      binding,
      ...(options.searchScope !== undefined ? { scope: options.searchScope } : {}),
      ...(options.searchLimit !== undefined ? { limit: options.searchLimit } : {}),
      ...identityOption,
      logger,
    }),
    zepContext: createZepContextTool({ client, binding, ...identityOption, logger }),
  };
}

/**
 * Hook run exactly once, immediately after a Zep user is newly created (never
 * on an already-exists conflict).
 *
 * Use this to configure per-user ontology, custom instructions, or user
 * summary instructions. Errors thrown by the hook are logged (at warn) and
 * swallowed — they never cause {@link ensureZepUserAndThread} to report
 * failure, since the user itself was created successfully.
 */
export type ZepUserCreatedHook = (
  client: ZepClient,
  userId: string,
) => void | Promise<void>;

/** Options for {@link ensureZepUserAndThread}. */
export interface EnsureIdentityOptions {
  /** A shared, initialized Zep client. */
  client: ZepClient;
  /** The Zep user ID. */
  userId: string;
  /** The Zep thread ID to create for this conversation. */
  threadId: string;
  /** User's first name — pass a real name to help Zep resolve identity. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
  /** User's email. */
  email?: string;
  /**
   * Runs exactly once, only when the user was newly created (not on an
   * already-exists conflict). Awaited immediately after user creation,
   * before the thread step; a rejection is logged and does not affect the
   * return value.
   */
  onUserCreated?: ZepUserCreatedHook;
  /** Logger for failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * Detect whether `error` represents a "resource already exists" conflict.
 *
 * Handles both typed and message-based shapes returned by the Zep SDK:
 *
 * - A 409 status code (`ConflictError`, or any `ZepError`-like object
 *   exposing `statusCode === 409`).
 * - A 400 `BadRequestError` (or similar) whose message mentions "already
 *   exists".
 * - An **untyped** error (no `statusCode`) whose string representation
 *   mentions "already exists" or "conflict" (fallback for untyped/legacy
 *   error shapes).
 *
 * A typed error with any other status code (e.g. a 500 whose message happens
 * to mention "conflict") is a genuine failure, not an already-exists
 * conflict.
 */
function isAlreadyExistsError(error: unknown): boolean {
  const statusCode = (error as { statusCode?: unknown } | null)?.statusCode;
  if (statusCode === 409) {
    return true;
  }

  const text = errorMessage(error).toLowerCase();
  if (statusCode === 400 && text.includes("already exists")) {
    return true;
  }

  // Fallback heuristic for untyped/legacy error shapes only: an error that
  // carries a known non-conflict status code is a genuine failure, no matter
  // what its message says.
  if (statusCode !== undefined) {
    return false;
  }
  return text.includes("already exists") || text.includes("conflict");
}

/**
 * Idempotently create the Zep user and thread for a conversation.
 *
 * Zep requires the user and thread to exist before messages are added. Call this
 * once, out-of-band, before the first turn (the Zep "create user → create thread"
 * step).
 *
 * Each step is create-then-catch-conflict: an "already exists" conflict
 * (see {@link isAlreadyExistsError}) is treated as success and does not run
 * {@link EnsureIdentityOptions.onUserCreated}. Any other failure (auth,
 * network, 5xx) is a genuine failure — it is logged at `warn` and this
 * function returns `false` rather than throwing, so callers on a hot path
 * (e.g. the start of every turn) are never crashed by a Zep outage.
 *
 * @returns `true` if the user and thread are ready, `false` if setup failed.
 */
export async function ensureZepUserAndThread(
  options: EnsureIdentityOptions,
): Promise<boolean> {
  const { client, userId, threadId, onUserCreated } = options;
  const logger = resolveLogger(options.logger);

  let userCreated = false;
  try {
    await client.user.add({
      userId,
      ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
      ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
      ...(options.email !== undefined ? { email: options.email } : {}),
    });
    userCreated = true;
  } catch (error) {
    if (!isAlreadyExistsError(error)) {
      logger.warn(`[zep] Failed to ensure user: ${errorMessage(error)}`);
      return false;
    }
    // Already exists — proceed to ensure the thread.
  }

  // The hook must run before the thread step: a transient thread.create
  // failure would otherwise skip it forever (a retry hits the already-exists
  // path with userCreated=false).
  if (userCreated && onUserCreated) {
    try {
      await onUserCreated(client, userId);
    } catch (error) {
      logger.warn(`[zep] onUserCreated hook failed: ${errorMessage(error)}`);
    }
  }

  try {
    await client.thread.create({ threadId, userId });
  } catch (error) {
    if (!isAlreadyExistsError(error)) {
      logger.warn(`[zep] Failed to ensure thread: ${errorMessage(error)}`);
      return false;
    }
    // Already exists — that's fine.
  }

  return true;
}
