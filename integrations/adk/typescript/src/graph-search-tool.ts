/**
 * `ZepGraphSearchTool` — a model-callable ADK tool for searching a Zep
 * knowledge graph on demand.
 *
 * Unlike `ZepContextTool` (which injects context automatically on every turn),
 * this tool appears in the model's tool list and is called when the model
 * decides it needs to look something up.
 *
 * Search target resolution:
 *   - If `graphId` is set, every search targets that standalone graph.
 *   - Otherwise the current user's graph is searched, with the user ID resolved
 *     from explicit options → `zep_user_id` state key → the ADK `userId`.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import {
  BaseTool,
  type Context,
} from "@google/adk";
import { type FunctionDeclaration, Type } from "@google/genai";
import {
  resolveIdentity,
  type AdkContextLike,
  type ZepIdentityOptions,
} from "./identity.js";
import { defaultLogger, type Logger } from "./logging.js";

const DEFAULT_DESCRIPTION =
  "Search the user's knowledge graph for facts, entities, or prior messages " +
  "from previous conversations. Use this to look up specific details the user " +
  "has shared before.";

/** Options for the {@link ZepGraphSearchTool} constructor. */
export interface ZepGraphSearchToolOptions extends ZepIdentityOptions {
  /** An initialised `ZepClient`. The caller owns its lifecycle. */
  zep: ZepClient;
  /**
   * Fixed standalone graph ID. When set, all searches target this graph
   * regardless of the active user. When omitted, the current user's graph is
   * searched. Mutually exclusive with per-user search.
   */
  graphId?: string;
  /** Tool name shown to the model. Defaults to `"zep_graph_search"`. */
  name?: string;
  /** Tool description shown to the model. */
  description?: string;
  /** Search scope. Defaults to `"edges"`. */
  scope?: Zep.GraphSearchScope;
  /** Reranking method. Defaults to `"rrf"`. */
  reranker?: Zep.Reranker;
  /** Maximum number of results. Defaults to `10`. */
  limit?: number;
  /** Logger for Zep failures. Defaults to a `console`-backed logger. */
  logger?: Logger;
}

/** Arguments the model supplies when calling the tool. */
interface GraphSearchArgs {
  query?: unknown;
}

/**
 * Search scopes this tool knows how to format. Every member of Zep's
 * `GraphSearchScope` is handled, so an unsupported scope is rejected at
 * construction rather than silently returning "No results found.".
 */
const SUPPORTED_SCOPES = [
  "auto",
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
] as const satisfies readonly Zep.GraphSearchScope[];

/**
 * A model-callable tool that searches a Zep knowledge graph.
 *
 * The scope, reranker, and limit are pinned at construction time, so the model
 * only chooses the `query` string. Errors are returned to the model as text
 * rather than thrown, so a Zep failure cannot crash the agent run.
 *
 * @example
 * ```ts
 * import { LlmAgent } from "@google/adk";
 * import { ZepClient } from "@getzep/zep-cloud";
 * import { ZepGraphSearchTool } from "@getzep/zep-adk";
 *
 * const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
 * const agent = new LlmAgent({
 *   name: "memory_agent",
 *   model: "gemini-2.5-flash",
 *   instruction: "You are a helpful assistant.",
 *   tools: [new ZepGraphSearchTool({ zep, userId: "user-123" })],
 * });
 * ```
 */
export class ZepGraphSearchTool extends BaseTool {
  private readonly zep: ZepClient;
  private readonly logger: Logger;
  private readonly graphId?: string;
  private readonly scope: Zep.GraphSearchScope;
  private readonly reranker: Zep.Reranker;
  private readonly limit: number;
  private readonly identity: ZepIdentityOptions;
  private readonly declaration: FunctionDeclaration;

  constructor(options: ZepGraphSearchToolOptions) {
    super({
      name: options.name ?? "zep_graph_search",
      description: options.description ?? DEFAULT_DESCRIPTION,
    });
    this.zep = options.zep;
    this.logger = options.logger ?? defaultLogger;
    this.graphId = options.graphId;
    const scope = options.scope ?? "edges";
    if (!SUPPORTED_SCOPES.includes(scope)) {
      throw new Error(
        `Unsupported Zep graph search scope: '${scope}'. ` +
          `Supported scopes are: ${SUPPORTED_SCOPES.join(", ")}.`,
      );
    }
    this.scope = scope;
    this.reranker = options.reranker ?? "rrf";
    this.limit = options.limit ?? 10;
    this.identity = {
      userId: options.userId,
      threadId: options.threadId,
      firstName: options.firstName,
      lastName: options.lastName,
      email: options.email,
    };
    this.declaration = {
      name: this.name,
      description: this.description,
      parameters: {
        type: Type.OBJECT,
        properties: {
          query: {
            type: Type.STRING,
            description: "Natural-language search query (max 400 characters).",
          },
        },
        required: ["query"],
      },
    };
  }

  /** Exposes the `query` parameter so the model can call this tool. */
  override _getDeclaration(): FunctionDeclaration {
    return this.declaration;
  }

  /**
   * Run the graph search and return formatted results as text for the model.
   *
   * Returns a human-readable error string (never throws) when the search
   * target cannot be resolved or the Zep call fails.
   */
  override async runAsync(request: {
    args: Record<string, unknown>;
    toolContext: Context;
  }): Promise<unknown> {
    const { args, toolContext } = request;
    const query = (args as GraphSearchArgs).query;
    if (typeof query !== "string" || query.trim().length === 0) {
      return "Error: a non-empty 'query' string is required.";
    }

    const target = this.resolveTarget(toolContext);
    if ("error" in target) {
      return target.error;
    }

    try {
      const results = await this.zep.graph.search({
        ...target,
        query,
        scope: this.scope,
        reranker: this.reranker,
        limit: this.limit,
      });
      return this.formatResults(results);
    } catch (error) {
      this.logger.warn("Zep graph search failed", error);
      return `Graph search failed: ${error instanceof Error ? error.message : String(error)}`;
    }
  }

  private resolveTarget(
    context: AdkContextLike,
  ): { graphId: string } | { userId: string } | { error: string } {
    if (this.graphId) {
      return { graphId: this.graphId };
    }
    try {
      return { userId: resolveIdentity(context, this.identity).userId };
    } catch (error) {
      this.logger.warn("Could not resolve user ID for graph search", error);
      return { error: "Error: could not determine which graph to search." };
    }
  }

  private formatResults(results: Zep.GraphSearchResults): string {
    // Auto scope returns a pre-assembled context string.
    if (this.scope === "auto") {
      const context = results.context?.trim();
      return context && context.length > 0 ? context : "No results found.";
    }

    const lines: string[] = [];
    if (this.scope === "edges") {
      for (const edge of results.edges ?? []) {
        if (edge.fact) lines.push(`- ${edge.fact}`);
      }
    } else if (this.scope === "nodes") {
      for (const node of results.nodes ?? []) {
        const name = node.name || "Entity";
        if (node.summary) lines.push(`- ${name}: ${node.summary}`);
      }
    } else if (this.scope === "episodes") {
      for (const episode of results.episodes ?? []) {
        if (episode.content) lines.push(`- ${episode.content}`);
      }
    } else if (this.scope === "observations") {
      for (const observation of results.observations ?? []) {
        const summary = observation.summary;
        if (summary) lines.push(`- ${summary}`);
      }
    } else if (this.scope === "thread_summaries") {
      for (const summaryNode of results.threadSummaries ?? []) {
        const summary = summaryNode.summary;
        if (summary) lines.push(`- ${summary}`);
      }
    }

    return lines.length > 0 ? lines.join("\n") : "No results found.";
  }
}
