import type { ZepClient } from "@getzep/zep-cloud";
import type { ZepIdentityResolver, ZepThreadBinding, ZepLogger } from "./types.js";
import { createZepRememberTool } from "./remember-tool.js";
import type { ZepSearchScope } from "./search-tool.js";
import { createZepSearchTool } from "./search-tool.js";
import { createZepContextTool } from "./context-tool.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";

/** Options for {@link createZepToolset}. */
export interface ZepToolsetOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /**
   * Graph + thread binding for the tools. Supply the `graphUuid` of a user
   * graph or of a standalone graph, and the `threadUuid` of the conversation.
   */
  binding: ZepThreadBinding;
  /** Pin the search scope (default `"edges"`). */
  searchScope?: ZepSearchScope;
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
 * Hook run exactly once, immediately after a Zep user is created.
 *
 * Use this to configure per-user ontology, custom instructions, or user
 * summary instructions. The hook receives the UUID of the new user. Errors
 * thrown by the hook are logged (at warn) and swallowed — they never cause
 * {@link createZepUserAndThread} to report failure, because the user itself
 * was created successfully.
 */
export type ZepUserCreatedHook = (
  client: ZepClient,
  userUuid: string,
) => void | Promise<void>;

/** Options for {@link createZepUserAndThread}. */
export interface CreateIdentityOptions {
  /** A shared, initialized Zep client. */
  client: ZepClient;
  /**
   * An optional developer-assigned name for the user. Zep does not use it as
   * an address; the returned `userUuid` is the address.
   */
  userId?: string;
  /**
   * An optional developer-assigned name for the thread. Zep does not use it
   * as an address; the returned `threadUuid` is the address.
   */
  threadId?: string;
  /** User's first name — pass a real name to help Zep resolve identity. */
  firstName?: string;
  /** User's last name. */
  lastName?: string;
  /** User's email. */
  email?: string;
  /**
   * Runs exactly once, immediately after the user is created and before the
   * thread step. A rejection is logged and does not affect the return value.
   */
  onUserCreated?: ZepUserCreatedHook;
  /** Logger for failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * The server-generated addresses of a new Zep user and thread.
 *
 * Store all three values in your own database. Every later call to Zep and
 * to this integration takes a UUID, never a name.
 */
export interface ZepIdentity {
  /** The UUID of the new user. */
  userUuid: string;
  /** The UUID of the user's graph. Bind the tools to it. */
  graphUuid: string;
  /** The UUID of the new thread. */
  threadUuid: string;
}

/**
 * Create the Zep user and thread for a conversation, and return their UUIDs.
 *
 * Zep requires the user and thread to exist before messages are added. Call
 * this once, out-of-band, before the first turn (the Zep "create user →
 * create thread" step), then store the returned UUIDs in your own database.
 * Zep v4 addresses every later call by UUID, so this function is **not**
 * idempotent on a name: a second call creates a second user.
 *
 * A failure (auth, network, 5xx) is logged at `warn` and reported as `null`
 * rather than thrown, so a Zep outage never crashes the caller.
 *
 * @returns The new {@link ZepIdentity}, or `null` when the setup failed.
 */
export async function createZepUserAndThread(
  options: CreateIdentityOptions,
): Promise<ZepIdentity | null> {
  const { client, onUserCreated } = options;
  const logger = resolveLogger(options.logger);

  let userUuid: string;
  let graphUuid: string;
  try {
    const user = await client.user.create({
      ...(options.userId !== undefined ? { userId: options.userId } : {}),
      ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
      ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
      ...(options.email !== undefined ? { email: options.email } : {}),
    });
    if (!user.uuid || !user.graphUuid) {
      logger.warn("[zep] The created user has no uuid or graphUuid.");
      return null;
    }
    userUuid = user.uuid;
    graphUuid = user.graphUuid;
  } catch (error) {
    logger.warn(`[zep] Failed to create the user: ${errorMessage(error)}`);
    return null;
  }

  if (onUserCreated) {
    try {
      await onUserCreated(client, userUuid);
    } catch (error) {
      logger.warn(`[zep] onUserCreated hook failed: ${errorMessage(error)}`);
    }
  }

  try {
    const thread = await client.thread.create({
      userUuid,
      ...(options.threadId !== undefined ? { threadId: options.threadId } : {}),
    });
    if (!thread.uuid) {
      logger.warn("[zep] The created thread has no uuid.");
      return null;
    }
    return { userUuid, graphUuid, threadUuid: thread.uuid };
  } catch (error) {
    logger.warn(`[zep] Failed to create the thread: ${errorMessage(error)}`);
    return null;
  }
}
