import { tool } from "ai";
import type { ToolSet } from "ai";
import { z } from "zod";
import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { ZepBinding, ZepLogger } from "./types.js";
import {
  GRAPH_MAX_CHARS,
  MESSAGE_MAX_CHARS,
  errorMessage,
  resolveGraphTarget,
  resolveLogger,
  toRoleType,
  truncateForZep,
} from "./zep-utils.js";

/** Options shared by every tool factory. */
interface BaseToolOptions {
  /** A shared, initialized Zep client. The caller owns its lifecycle. */
  client: ZepClient;
  /** Logger for Zep failures. Defaults to `console`. */
  logger?: ZepLogger;
}

/** Options for {@link createZepSearchTool}. */
export interface ZepSearchToolOptions extends BaseToolOptions {
  /** The graph to search — a user graph (`userId`) or standalone graph (`graphId`). */
  binding: ZepBinding;
  /** Override the tool description shown to the model. */
  description?: string;
  /**
   * Fixed search scope. Defaults to `"edges"` (facts/relationships), the most
   * useful scope for an agent recalling discrete claims. Pinning a scope hides
   * it from the model so it cannot choose a less useful one.
   */
  scope?: Zep.GraphSearchScope;
  /** Maximum number of results to retrieve (Zep caps non-auto scopes at 50). */
  limit?: number;
  /** Optional reranker (default Zep RRF). */
  reranker?: Zep.Reranker;
  /** Optional Zep search filters (entity/edge types, properties, dates). */
  searchFilters?: Zep.SearchFilters;
}

/** Options for {@link createZepRememberTool}. */
export interface ZepRememberToolOptions extends BaseToolOptions {
  /**
   * The graph this tool writes to, plus the thread used to record conversation.
   *
   * - When `threadId` and `userId` are present, conversational content (a
   *   `role` is supplied) is persisted via `thread.addMessages` (records history
   *   *and* ingests into the user graph). Non-conversational data always uses
   *   `graph.add`.
   * - When only `graphId` (or `userId` without a thread) is present, everything
   *   is ingested via `graph.add`.
   */
  binding: ZepBinding & { threadId?: string };
  /** Override the tool description shown to the model. */
  description?: string;
  /** Default `name` recorded on conversational messages (e.g. the user's real name). */
  defaultMessageName?: string;
}

/** Options for {@link createZepContextTool}. */
export interface ZepContextToolOptions extends BaseToolOptions {
  /**
   * The thread whose Context Block to fetch. The block is assembled from the
   * **entire user graph**; the thread only scopes relevance.
   */
  threadId: string;
  /** Override the tool description shown to the model. */
  description?: string;
  /** Optional Zep context template ID for custom Context Block formatting. */
  templateId?: string;
}

const searchInputSchema = z.object({
  query: z
    .string()
    .min(1)
    .max(400)
    .describe(
      "What to look up in long-term memory (max 400 characters). Phrase it as " +
        "the information you need, e.g. 'where the user lives'.",
    ),
});

const rememberInputSchema = z.object({
  content: z
    .string()
    .min(1)
    .describe("The fact, message, or piece of information to remember."),
  role: z
    .string()
    .optional()
    .describe(
      "Who the content is from when it is a conversational message: 'user', " +
        "'assistant', 'system', 'tool', or 'function'. Omit for general facts.",
    ),
});

const contextInputSchema = z.object({});

/** Format a node-like result ("name: summary" or just "name"). */
function nameAndSummary(n: { name?: string; summary?: string }): string | undefined {
  if (!n.name) return undefined;
  return n.summary ? `${n.name}: ${n.summary}` : n.name;
}

const isNonEmpty = (s: string | undefined): s is string => Boolean(s);

/**
 * Extract human-readable strings from a Zep search result for the active scope.
 *
 * The switch is exhaustive over every {@link Zep.GraphSearchScope}; the `never`
 * default makes a new SDK scope a compile error rather than a silently-empty
 * result.
 */
function extractResults(
  result: Zep.GraphSearchResults,
  scope: Zep.GraphSearchScope,
): string[] {
  switch (scope) {
    case "auto": {
      const ctx = result.context?.trim();
      return ctx ? [ctx] : [];
    }
    case "edges":
      return (result.edges ?? []).map((e) => e.fact).filter(isNonEmpty);
    case "nodes":
      return (result.nodes ?? []).map(nameAndSummary).filter(isNonEmpty);
    case "episodes":
      return (result.episodes ?? []).map((e) => e.content).filter(isNonEmpty);
    case "thread_summaries":
      return (result.threadSummaries ?? []).map(nameAndSummary).filter(isNonEmpty);
    case "observations":
      return (result.observations ?? []).map(nameAndSummary).filter(isNonEmpty);
    default: {
      const _exhaustive: never = scope;
      return _exhaustive;
    }
  }
}

/**
 * Build a model-callable AI SDK tool that **searches** the bound Zep graph and
 * returns relevant facts.
 *
 * Drop it into `generateText`/`streamText`'s `tools` record so the model can
 * decide *when* and *what* to recall during a tool loop. Scope, limit, reranker,
 * and filters are pinned at construction and hidden from the model.
 *
 * A Zep failure is logged (no PII) and returned as `found: false` with an empty
 * list; it never throws.
 */
export function createZepSearchTool(options: ZepSearchToolOptions) {
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);
  const target = resolveGraphTarget(binding);
  const scope: Zep.GraphSearchScope = options.scope ?? "edges";

  return tool({
    description:
      options.description ??
      "Search long-term memory for facts about the user or domain learned in " +
        "previous turns or conversations. Use this to recall specific details " +
        "the user shared before.",
    inputSchema: searchInputSchema,
    execute: async ({ query }): Promise<{ facts: string[]; found: boolean }> => {
      const trimmed = query?.trim();
      if (!trimmed) return { facts: [], found: false };
      if (!target) {
        logger.warn("[zep-search] No userId or graphId bound; skipping search.");
        return { facts: [], found: false };
      }

      try {
        const request: Zep.GraphSearchQuery = {
          ...target,
          query: trimmed,
          scope,
          ...(options.limit !== undefined ? { limit: options.limit } : {}),
          ...(options.reranker !== undefined ? { reranker: options.reranker } : {}),
          ...(options.searchFilters !== undefined
            ? { searchFilters: options.searchFilters }
            : {}),
        };
        const result = await client.graph.search(request);
        const facts = extractResults(result, scope);
        return { facts, found: facts.length > 0 };
      } catch (error) {
        logger.warn(`[zep-search] Zep graph search failed: ${errorMessage(error)}`);
        return { facts: [], found: false };
      }
    },
  });
}

/**
 * Build a model-callable AI SDK tool that **persists** information into Zep.
 *
 * Conversational content (a `role` is provided and a thread + user are bound) is
 * written with `thread.addMessages`, which records history and ingests into the
 * user graph. Everything else is ingested with `graph.add` (`type: "text"`).
 *
 * Zep ingestion is **asynchronous**: a just-stored fact is not instantly
 * retrievable. The tool reports success once Zep accepts the data.
 *
 * Over-long content is truncated (lengths-only warning, never content). A Zep
 * failure is logged and surfaced as `stored: false`; it never throws.
 */
export function createZepRememberTool(options: ZepRememberToolOptions) {
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);
  const target = resolveGraphTarget(binding);
  const threadId = binding.threadId;

  return tool({
    description:
      options.description ??
      "Persist a fact, preference, or message to long-term memory so it can be " +
        "recalled in future turns and future conversations.",
    inputSchema: rememberInputSchema,
    execute: async ({
      content,
      role,
    }): Promise<{ stored: boolean; message: string }> => {
      const trimmed = content?.trim();
      if (!trimmed) {
        return { stored: false, message: "Nothing to remember: content was empty." };
      }
      if (!target) {
        logger.warn("[zep-remember] No userId or graphId bound; skipping persist.");
        return {
          stored: false,
          message: "Memory is not configured (no user or graph bound).",
        };
      }

      try {
        // Conversational content with a bound thread + user → thread.addMessages.
        // Capped at Zep's 4,096-char message limit.
        if (role && threadId && binding.userId) {
          await client.thread.addMessages(threadId, {
            messages: [
              {
                role: toRoleType(role, logger),
                content: truncateForZep(trimmed, MESSAGE_MAX_CHARS, "zep-remember", logger),
                ...(options.defaultMessageName !== undefined
                  ? { name: options.defaultMessageName }
                  : {}),
              },
            ],
          });
          return { stored: true, message: "Saved to conversation memory." };
        }

        // Everything else → graph.add as text. Capped at Zep's 10,000-char
        // graph.add limit (never silently dropped — truncate with a warning).
        await client.graph.add({
          ...target,
          type: "text",
          data: truncateForZep(trimmed, GRAPH_MAX_CHARS, "zep-remember", logger),
        });
        return { stored: true, message: "Saved to long-term memory." };
      } catch (error) {
        logger.warn(`[zep-remember] Failed to persist to Zep: ${errorMessage(error)}`);
        return {
          stored: false,
          message: "Could not save to memory right now; continuing without it.",
        };
      }
    },
  });
}

/**
 * Build a model-callable AI SDK tool that returns the **Context Block** for the
 * bound user via `thread.getUserContext`.
 *
 * One call returns a prompt-ready string (user summary + relevant facts and
 * entities) assembled from the *whole* user graph, with the thread's most recent
 * messages used to focus relevance. It is the tool-callable counterpart to
 * {@link createZepMiddleware}'s system-message injection — useful when you want
 * the model to pull in context on demand inside a tool loop.
 *
 * A Zep failure is logged (no PII) and returned as `found: false` with an empty
 * string; it never throws.
 */
export function createZepContextTool(options: ZepContextToolOptions) {
  const { client, threadId } = options;
  const logger = resolveLogger(options.logger);

  return tool({
    description:
      options.description ??
      "Retrieve everything currently known about the user — a summary plus " +
        "relevant facts from previous conversations — to ground your response.",
    inputSchema: contextInputSchema,
    execute: async (): Promise<{ context: string; found: boolean }> => {
      if (!threadId) {
        logger.warn("[zep-context] No threadId bound; skipping context retrieval.");
        return { context: "", found: false };
      }
      try {
        const response = await client.thread.getUserContext(
          threadId,
          options.templateId ? { templateId: options.templateId } : {},
        );
        const context = response.context?.trim() ?? "";
        return { context, found: context.length > 0 };
      } catch (error) {
        logger.warn(`[zep-context] Failed to retrieve Zep context: ${errorMessage(error)}`);
        return { context: "", found: false };
      }
    },
  });
}

/** Options for {@link createZepTools}. */
export interface ZepToolsOptions extends Omit<BaseToolOptions, "client"> {
  /**
   * Identity + thread binding. For a personalized conversational agent supply
   * `userId` and `threadId`; for a shared knowledge base supply `graphId`.
   * `zepContext` requires a `threadId`; without one it returns a graceful empty
   * result.
   */
  binding: ZepBinding & { threadId?: string };
  /** Pin the search scope (default `"edges"`). */
  searchScope?: Zep.GraphSearchScope;
  /** Pin the search result limit. */
  searchLimit?: number;
  /** Default speaker name recorded on conversational messages persisted by `zepRemember`. */
  defaultMessageName?: string;
}

/**
 * The Zep tool set, keyed for direct spread into a `generateText`/`streamText`
 * `tools` record:
 *
 * ```ts
 * const tools = createZepTools(client, { userId, threadId });
 * await generateText({ model, prompt, tools, stopWhen: stepCountIs(5) });
 * ```
 */
export type ZepTools = ToolSet & {
  /** Search the bound graph for relevant facts (`graph.search`). */
  zepSearch: ReturnType<typeof createZepSearchTool>;
  /** Persist a message or fact (`thread.addMessages` / `graph.add`). */
  zepRemember: ReturnType<typeof createZepRememberTool>;
  /** Retrieve the whole-user-graph Context Block (`thread.getUserContext`). */
  zepContext: ReturnType<typeof createZepContextTool>;
};

/**
 * Build the full set of Zep tools bound to a single client and binding.
 *
 * Spread the result into a `generateText`/`streamText` `tools` record. Each tool
 * handles Zep failures gracefully and never throws — a memory outage cannot
 * crash the host call. `zepContext` is included only when a `threadId` is bound
 * (it has no meaning without one).
 *
 * @param client - A shared, initialized Zep client.
 * @param options - The binding plus optional search/persist configuration.
 */
export function createZepTools(client: ZepClient, options: ZepToolsOptions): ZepTools {
  const { binding } = options;
  const logger = resolveLogger(options.logger);

  const tools = {
    zepSearch: createZepSearchTool({
      client,
      binding,
      ...(options.searchScope !== undefined ? { scope: options.searchScope } : {}),
      ...(options.searchLimit !== undefined ? { limit: options.searchLimit } : {}),
      logger,
    }),
    zepRemember: createZepRememberTool({
      client,
      binding,
      ...(options.defaultMessageName !== undefined
        ? { defaultMessageName: options.defaultMessageName }
        : {}),
      logger,
    }),
    zepContext: createZepContextTool({
      client,
      threadId: binding.threadId ?? "",
      logger,
    }),
  };
  return tools as ZepTools;
}
