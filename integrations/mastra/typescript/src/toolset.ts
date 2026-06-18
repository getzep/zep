import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { ZepThreadBinding, ZepLogger } from "./types.js";
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
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);

  return {
    zepRemember: createZepRememberTool({
      client,
      binding,
      ...(options.defaultMessageName !== undefined
        ? { defaultMessageName: options.defaultMessageName }
        : {}),
      logger,
    }),
    zepSearch: createZepSearchTool({
      client,
      binding,
      ...(options.searchScope !== undefined ? { scope: options.searchScope } : {}),
      ...(options.searchLimit !== undefined ? { limit: options.searchLimit } : {}),
      logger,
    }),
    zepContext: createZepContextTool({ client, binding, logger }),
  };
}

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
  /** Logger for failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/**
 * Idempotently create the Zep user and thread for a conversation.
 *
 * Zep requires the user and thread to exist before messages are added. Call this
 * once, out-of-band, before the first turn (the Zep "create user → create thread"
 * step). Already-existing resources are treated as success. Failures are logged
 * and reported via the return value rather than thrown.
 *
 * @returns `true` if the user and thread are ready, `false` if setup failed.
 */
export async function ensureZepUserAndThread(
  options: EnsureIdentityOptions,
): Promise<boolean> {
  const { client, userId, threadId } = options;
  const logger = resolveLogger(options.logger);

  try {
    try {
      await client.user.add({
        userId,
        ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
        ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
        ...(options.email !== undefined ? { email: options.email } : {}),
      });
    } catch (error) {
      // A 409/duplicate means the user already exists — that's fine. Re-raise
      // only if a subsequent thread.create also fails.
      logger.debug?.(`[zep] user.add: ${errorMessage(error)} (may already exist)`);
    }

    await client.thread.create({ threadId, userId });
    return true;
  } catch (error) {
    const message = errorMessage(error);
    // Treat "already exists" style conflicts as success.
    if (/exist|conflict|409|duplicate/i.test(message)) {
      return true;
    }
    logger.warn(`[zep] Failed to ensure user/thread: ${message}`);
    return false;
  }
}
