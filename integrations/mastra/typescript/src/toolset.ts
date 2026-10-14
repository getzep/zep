import type { ZepClient } from "@getzep/zep-cloud";
import type { ZepIdentityResolver, ZepThreadBinding, ZepLogger } from "./types.js";
import { createZepRememberTool } from "./remember-tool.js";
import { createZepSearchTool, type ZepGraphSearchScope } from "./search-tool.js";
import { createZepContextTool } from "./context-tool.js";
import { errorMessage, resolveLogger } from "./zep-utils.js";

/** Options for {@link createZepToolset}. */
export interface ZepToolsetOptions {
  client: ZepClient;
  binding: ZepThreadBinding;
  searchScope?: ZepGraphSearchScope;
  searchLimit?: number;
  defaultMessageName?: string;
  resolveIdentity?: ZepIdentityResolver;
  logger?: ZepLogger;
}

/** Tools returned by {@link createZepToolset}. */
export interface ZepToolset {
  zepRemember: ReturnType<typeof createZepRememberTool>;
  zepSearch: ReturnType<typeof createZepSearchTool>;
  zepContext: ReturnType<typeof createZepContextTool>;
}

/** Build the tools for one graph and thread binding. */
export function createZepToolset(options: ZepToolsetOptions): ZepToolset {
  const { client, binding, resolveIdentity } = options;
  const logger = resolveLogger(options.logger);
  const identityOption = resolveIdentity ? { resolveIdentity } : {};

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
      ...(options.searchScope ? { scope: options.searchScope } : {}),
      ...(options.searchLimit !== undefined ? { limit: options.searchLimit } : {}),
      ...identityOption,
      logger,
    }),
    zepContext: createZepContextTool({ client, binding, ...identityOption, logger }),
  };
}

/** Runs after Zep creates a user and before the helper creates a thread. */
export type ZepUserCreatedHook = (
  client: ZepClient,
  userUuid: string,
) => void | Promise<void>;

/** Options for {@link createZepUserAndThread}. */
export interface CreateUserAndThreadOptions {
  client: ZepClient;
  firstName?: string;
  lastName?: string;
  email?: string;
  metadata?: Record<string, unknown>;
  timeZone?: string;
  disableDefaultOntology?: boolean;
  onUserCreated?: ZepUserCreatedHook;
  logger?: ZepLogger;
}

/** UUIDs for the created user, graph, and thread. */
export interface CreatedZepIdentity {
  userUuid: string;
  graphUuid: string;
  threadUuid: string;
}

/**
 * Create a user and thread with server-generated UUIDs.
 *
 * The function returns `null` and logs a warning when a Zep call fails.
 */
export async function createZepUserAndThread(
  options: CreateUserAndThreadOptions,
): Promise<CreatedZepIdentity | null> {
  const logger = resolveLogger(options.logger);
  try {
    const user = await options.client.user.create({
      ...(options.firstName !== undefined ? { firstName: options.firstName } : {}),
      ...(options.lastName !== undefined ? { lastName: options.lastName } : {}),
      ...(options.email !== undefined ? { email: options.email } : {}),
      ...(options.metadata !== undefined ? { metadata: options.metadata } : {}),
      ...(options.timeZone !== undefined ? { timeZone: options.timeZone } : {}),
      ...(options.disableDefaultOntology !== undefined
        ? { disableDefaultOntology: options.disableDefaultOntology }
        : {}),
    });
    if (!user.uuid || !user.graphUuid) {
      logger.warn("[zep] User creation returned no UUID.");
      return null;
    }

    if (options.onUserCreated) {
      try {
        await options.onUserCreated(options.client, user.uuid);
      } catch (error) {
        logger.warn(`[zep] onUserCreated hook failed: ${errorMessage(error)}`);
      }
    }

    const thread = await options.client.thread.create({ userUuid: user.uuid });
    if (!thread.uuid) {
      logger.warn("[zep] Thread creation returned no UUID.");
      return null;
    }
    return {
      userUuid: user.uuid,
      graphUuid: user.graphUuid,
      threadUuid: thread.uuid,
    };
  } catch (error) {
    logger.warn(`[zep] Failed to create user and thread: ${errorMessage(error)}`);
    return null;
  }
}
