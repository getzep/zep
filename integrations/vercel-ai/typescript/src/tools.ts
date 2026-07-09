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

/**
 * Every `graph.search` parameter that can be pinned or exposed to the model,
 * by name. Keys match the Zep SDK's `graph.search()` camelCase kwargs.
 */
export type ZepSearchParamName = "scope" | "reranker" | "limit" | "mmrLambda" | "centerNodeUuid";

/** Options for {@link createZepSearchTool}. */
export interface ZepSearchToolOptions extends BaseToolOptions {
  /** The graph to search — a user graph (`userId`) or standalone graph (`graphId`). */
  binding: ZepBinding;
  /** Override the tool description shown to the model. */
  description?: string;
  /**
   * Pin a `graph.search` parameter to a fixed value: hidden from the model's
   * tool schema and always sent with the given value, regardless of what the
   * model would otherwise choose.
   */
  pinnedParams?: Partial<Record<ZepSearchParamName, unknown>>;
  /**
   * Hide a `graph.search` parameter from the model's tool schema WITHOUT
   * pinning it — the parameter is simply omitted from the SDK call, so Zep's
   * own server-side default applies.
   */
  hiddenParams?: Set<ZepSearchParamName> | ZepSearchParamName[];
  /**
   * Optional Zep search filters (entity/edge types, properties, dates).
   * Constructor-only — never exposed to the model.
   */
  searchFilters?: Zep.SearchFilters;
  /**
   * Node UUIDs seeding a breadth-first search. Constructor-only — never
   * exposed to the model.
   */
  bfsOriginNodeUuids?: string[];
  /**
   * Deprecated back-compat alias for `pinnedParams.scope`. Defaults to
   * `"edges"` remain the model's default when this and `pinnedParams.scope`
   * are both unset — the parameter stays exposed.
   */
  scope?: Zep.GraphSearchScope;
  /** Deprecated back-compat alias for `pinnedParams.limit`. */
  limit?: number;
  /** Deprecated back-compat alias for `pinnedParams.reranker`. */
  reranker?: Zep.Reranker;
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

/** Zep's supported search scopes (`GraphSearchScope`), model-exposable subset. */
const SCOPE_VALUES = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;

/** Zep's supported rerankers (`Reranker`). */
const RERANKER_VALUES = ["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"] as const;

const DEFAULT_SEARCH_SCOPE: Zep.GraphSearchScope = "edges";

/** Zep's server-side ceiling for the `graph.search` result `limit`. */
const MAX_SEARCH_LIMIT = 50;

/**
 * Rerankers Zep rejects outright when `scope` is `"auto"` (auto search always
 * uses RRF internally and ignores `reranker` entirely).
 */
const AUTO_INCOMPATIBLE_RERANKERS: ReadonlySet<string> = new Set([
  "node_distance",
  "episode_mentions",
]);

/**
 * Clamp a search limit into Zep's accepted range `[1, 50]` so the call never
 * 400s. Warns (via `warn`) only when the limit exceeds the ceiling.
 */
function clampSearchLimit(limit: number, warn: (message: string) => void): number {
  if (limit > MAX_SEARCH_LIMIT) {
    warn(
      `[zep-search] limit ${limit} exceeds Zep ceiling ${MAX_SEARCH_LIMIT}; ` +
        `clamping to ${MAX_SEARCH_LIMIT}.`,
    );
    return MAX_SEARCH_LIMIT;
  }
  return limit < 1 ? 1 : limit;
}

const queryField = z
  .string()
  .min(1)
  .max(400)
  .describe(
    "What to look up in long-term memory (max 400 characters). Phrase it as " +
      "the information you need, e.g. 'where the user lives'.",
  );

/**
 * Zod field builders for each pin-or-exposable `graph.search` parameter,
 * matching the pydantic-ai sibling's `_SEARCH_PARAM_SPECS` shape (description,
 * default) so the model-facing contract stays consistent across languages.
 */
const SEARCH_FIELD_BUILDERS: Record<ZepSearchParamName, () => z.ZodTypeAny> = {
  scope: () =>
    z
      .enum(SCOPE_VALUES)
      .optional()
      .describe(
        "What to search for: 'edges' for facts and relationships, " +
          "'nodes' for entities and their summaries, " +
          "'episodes' for raw text data (unstructured text, messages, or JSON), " +
          "'observations' for derived memories, " +
          "'thread_summaries' for incremental thread summaries, " +
          "'auto' to let Zep decide the best mix of results. Defaults to 'edges'.",
      ),
  reranker: () =>
    z
      .enum(RERANKER_VALUES)
      .optional()
      .describe(
        "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), " +
          "'cross_encoder' (highest accuracy), 'episode_mentions' " +
          "(frequently referenced), 'node_distance' (near a specific entity). Defaults to 'rrf'.",
      ),
  limit: () =>
    z.number().int().optional().describe("Maximum number of results to return. Defaults to 10."),
  mmrLambda: () =>
    z
      .number()
      .optional()
      .describe(
        "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'.",
      ),
  centerNodeUuid: () =>
    z
      .string()
      .optional()
      .describe(
        "UUID of the center node for distance-based reranking. Required when reranker is 'node_distance'.",
      ),
};

/**
 * Build the model-facing zod input schema for the search tool, excluding
 * pinned/hidden parameters. `query` is always present and required.
 */
function buildSearchInputSchema(pinned: Set<ZepSearchParamName>, hidden: Set<ZepSearchParamName>) {
  const shape: Record<string, z.ZodTypeAny> = { query: queryField };
  for (const name of Object.keys(SEARCH_FIELD_BUILDERS) as ZepSearchParamName[]) {
    if (pinned.has(name) || hidden.has(name)) continue;
    shape[name] = SEARCH_FIELD_BUILDERS[name]();
  }
  return z.object(shape);
}

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

/** Normalize `hiddenParams` (array or `Set`) into a `Set`. */
function toParamSet(
  value: Set<ZepSearchParamName> | ZepSearchParamName[] | undefined,
): Set<ZepSearchParamName> {
  return value instanceof Set ? value : new Set(value ?? []);
}

/**
 * Build a model-callable AI SDK tool that **searches** the bound Zep graph and
 * returns relevant facts.
 *
 * Drop it into `generateText`/`streamText`'s `tools` record so the model can
 * decide *when* and *what* to recall during a tool loop.
 *
 * **Pin-or-expose.** Every `graph.search` parameter (`scope`, `reranker`,
 * `limit`, `mmrLambda`, `centerNodeUuid`) is exposed to the model in the
 * tool's Zod input schema by default. Use `pinnedParams` to fix a parameter
 * to a constant value and remove it from the schema (the model can no longer
 * choose it); use `hiddenParams` to remove a parameter from the schema
 * *without* pinning it — Zep's own server-side default applies, and the
 * parameter is simply omitted from the SDK call. `searchFilters` and
 * `bfsOriginNodeUuids` are always constructor-only. The legacy `scope` /
 * `reranker` / `limit` constructor args pin (and thus hide) their parameter,
 * same as passing it via `pinnedParams` — back-compat for the pre-pin-or-expose
 * API.
 *
 * A Zep failure is logged (no PII) and returned as `found: false` with an empty
 * list; it never throws.
 */
export function createZepSearchTool(options: ZepSearchToolOptions) {
  const { client, binding } = options;
  const logger = resolveLogger(options.logger);
  const target = resolveGraphTarget(binding);

  const pinnedValues: Partial<Record<ZepSearchParamName, unknown>> = {
    ...options.pinnedParams,
  };
  // Legacy constructor args pin (and thus hide) their parameter.
  if (options.scope !== undefined) pinnedValues.scope ??= options.scope;
  if (options.reranker !== undefined) pinnedValues.reranker ??= options.reranker;
  if (options.limit !== undefined) pinnedValues.limit ??= options.limit;

  // Clamp a pinned limit to Zep's ceiling at construction so the call never
  // 400s.
  if (typeof pinnedValues.limit === "number") {
    pinnedValues.limit = clampSearchLimit(pinnedValues.limit, (m) => logger.warn(m));
  }

  // Auto scope rejects node_distance/episode_mentions and ignores reranker
  // entirely. If scope is pinned to "auto" and reranker is also pinned,
  // resolve the conflict once, here, so the call path is always valid.
  if (pinnedValues.scope === "auto" && "reranker" in pinnedValues) {
    if (AUTO_INCOMPATIBLE_RERANKERS.has(String(pinnedValues.reranker))) {
      logger.warn(
        `[zep-search] reranker '${String(pinnedValues.reranker)}' is invalid for ` +
          "scope 'auto'; omitting reranker (auto search uses RRF).",
      );
    }
    delete pinnedValues.reranker;
  }

  const pinned = new Set(Object.keys(pinnedValues) as ZepSearchParamName[]);
  const hidden = toParamSet(options.hiddenParams);

  const inputSchema = buildSearchInputSchema(pinned, hidden);

  return tool({
    description:
      options.description ??
      "Search long-term memory for facts about the user or domain learned in " +
        "previous turns or conversations. Use this to recall specific details " +
        "the user shared before.",
    inputSchema,
    execute: async (input: Record<string, unknown>): Promise<{ facts: string[]; found: boolean }> => {
      const trimmed = typeof input.query === "string" ? input.query.trim() : "";
      if (!trimmed) return { facts: [], found: false };
      if (!target) {
        logger.warn("[zep-search] No userId or graphId bound; skipping search.");
        return { facts: [], found: false };
      }

      // Resolve each pin-or-exposable parameter: pinned > model-supplied >
      // Zep's own default (omitted from the call so the server applies it) —
      // except `scope`, which we default to "edges" client-side so
      // `extractResults` always knows which branch to read.
      const resolved: Partial<Record<ZepSearchParamName, unknown>> = {};
      for (const name of Object.keys(SEARCH_FIELD_BUILDERS) as ZepSearchParamName[]) {
        if (name in pinnedValues) {
          resolved[name] = pinnedValues[name];
        } else if (hidden.has(name)) {
          continue; // hidden, not pinned -> omit; Zep applies its own default
        } else if (input[name] !== undefined && input[name] !== null) {
          resolved[name] = input[name];
        }
      }

      const effectiveScope = (resolved.scope as Zep.GraphSearchScope | undefined) ?? DEFAULT_SEARCH_SCOPE;

      // Clamp a model-provided limit to Zep's ceiling — clamp, never reject:
      // the tool must not 400 on limit. (A pinned limit was already clamped at
      // construction, so this is a no-op for it.)
      if (typeof resolved.limit === "number") {
        resolved.limit = clampSearchLimit(resolved.limit, (m) => logger.warn(m));
      }

      // Auto search always uses RRF internally and ignores reranker entirely;
      // Zep rejects node_distance/episode_mentions outright. Drop any
      // (model-provided) reranker; warn only when Zep would have rejected it.
      if (effectiveScope === "auto" && "reranker" in resolved) {
        const droppedReranker = resolved.reranker;
        delete resolved.reranker;
        if (AUTO_INCOMPATIBLE_RERANKERS.has(String(droppedReranker))) {
          logger.warn(
            `[zep-search] reranker '${String(droppedReranker)}' is invalid for ` +
              "scope 'auto'; omitting reranker.",
          );
        }
      }

      try {
        // None/undefined-omission guard: only send a key when a value is
        // actually resolved — never an explicit null/undefined.
        const requestFields: Record<string, unknown> = { ...target, query: trimmed };
        for (const [name, value] of Object.entries(resolved)) {
          if (value !== undefined && value !== null) {
            requestFields[name] = value;
          }
        }
        if (!("scope" in requestFields)) {
          requestFields.scope = effectiveScope;
        }
        if (options.searchFilters !== undefined) {
          requestFields.searchFilters = options.searchFilters;
        }
        if (options.bfsOriginNodeUuids !== undefined) {
          requestFields.bfsOriginNodeUuids = options.bfsOriginNodeUuids;
        }
        const request = requestFields as unknown as Zep.GraphSearchQuery;

        const result = await client.graph.search(request);
        const facts = extractResults(result, effectiveScope);
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
