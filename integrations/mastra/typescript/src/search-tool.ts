import { createTool } from "@mastra/core/tools";
import type { ZepClient, Zep } from "@getzep/zep-cloud";
import { z } from "zod";
import type { ZepBinding, ZepIdentityResolver, ZepLogger } from "./types.js";
import {
  errorMessage,
  resolveGraphTarget,
  resolveLogger,
  resolveToolIdentity,
} from "./zep-utils.js";

export const ZEP_SEARCH_SCOPES = [
  "edges",
  "nodes",
  "episodes",
  "observations",
  "thread_summaries",
  "auto",
] as const;
export type ZepGraphSearchScope = (typeof ZEP_SEARCH_SCOPES)[number];

const RERANKERS = [
  "rrf",
  "mmr",
  "node_distance",
  "episode_mentions",
  "cross_encoder",
] as const;
type ZepReranker = (typeof RERANKERS)[number];
type PinnableParamName = "scope" | "reranker" | "limit" | "mmrLambda" | "centerNodeUuid";

export interface ZepSearchPinnableParams {
  scope?: ZepGraphSearchScope;
  reranker?: ZepReranker;
  limit?: number;
  mmrLambda?: number;
  centerNodeUuid?: string;
}

export interface ZepSearchToolOptions {
  client: ZepClient;
  binding: ZepBinding;
  id?: string;
  description?: string;
  pinnedParams?: ZepSearchPinnableParams;
  hiddenParams?: Set<PinnableParamName>;
  filters?: Zep.SearchFilters;
  bfsOriginNodeUuids?: string[];
  scope?: ZepGraphSearchScope;
  reranker?: ZepReranker;
  limit?: number;
  resolveIdentity?: ZepIdentityResolver;
  logger?: ZepLogger;
}

const outputSchema = z.object({
  facts: z.array(z.string()),
  found: z.boolean(),
});
type SearchOutput = z.infer<typeof outputSchema>;

function nameAndSummary(value: { name?: string; summary?: string }): string | undefined {
  if (value.name && value.summary) return `${value.name}: ${value.summary}`;
  return value.name ?? value.summary;
}

function strings(values: Array<string | undefined>): string[] {
  return values.filter((value): value is string => Boolean(value));
}

function pageSize(value: number): number {
  return Math.min(50, Math.max(1, Math.floor(value)));
}

export function createZepSearchTool(options: ZepSearchToolOptions) {
  const logger = resolveLogger(options.logger);
  const pinned: ZepSearchPinnableParams = {
    ...options.pinnedParams,
    ...(options.scope !== undefined ? { scope: options.scope } : {}),
    ...(options.reranker !== undefined ? { reranker: options.reranker } : {}),
    ...(options.limit !== undefined ? { limit: options.limit } : {}),
  };
  const hidden = new Set(options.hiddenParams ?? []);
  const names: PinnableParamName[] = [
    "scope",
    "reranker",
    "limit",
    "mmrLambda",
    "centerNodeUuid",
  ];
  for (const name of hidden) {
    if (!names.includes(name)) throw new Error(`Unknown hiddenParams entry: '${name}'.`);
  }
  if (pinned.limit !== undefined) {
    const clampedLimit = pageSize(pinned.limit);
    if (clampedLimit !== pinned.limit) {
      logger.warn("[zep-search] Pinned limit was clamped to the supported range.");
    }
    pinned.limit = clampedLimit;
  }
  const pinnedAutoIncompatibleReranker =
    pinned.scope === "auto" &&
    (pinned.reranker === "node_distance" || pinned.reranker === "episode_mentions");
  if (pinnedAutoIncompatibleReranker) {
    logger.warn(`[zep-search] Reranker '${pinned.reranker}' is not supported for auto search.`);
  }

  const fields: Record<string, z.ZodTypeAny> = {
    query: z.string().min(1).max(400),
  };
  if (pinned.scope === undefined && !hidden.has("scope")) {
    fields.scope = z.enum(ZEP_SEARCH_SCOPES).optional();
  }
  if (pinned.reranker === undefined && !hidden.has("reranker")) {
    fields.reranker = z.enum(RERANKERS).optional();
  }
  if (pinned.limit === undefined && !hidden.has("limit")) {
    fields.limit = z.number().int().optional();
  }
  if (pinned.mmrLambda === undefined && !hidden.has("mmrLambda")) {
    fields.mmrLambda = z.number().optional();
  }
  if (pinned.centerNodeUuid === undefined && !hidden.has("centerNodeUuid")) {
    fields.centerNodeUuid = z.string().optional();
  }
  const inputSchema = z.object(fields);
  type SearchInput = z.infer<typeof inputSchema> & {
    scope?: ZepGraphSearchScope;
    reranker?: ZepReranker;
    limit?: number;
    mmrLambda?: number;
    centerNodeUuid?: string;
  };

  return createTool({
    id: options.id ?? "zep-search",
    description:
      options.description ??
      "Search long-term memory for facts from earlier turns or conversations.",
    inputSchema,
    outputSchema,
    execute: async (
      input: SearchInput,
      context?: { requestContext?: unknown },
    ): Promise<SearchOutput> => {
      const query = input.query?.trim();
      if (!query) return { facts: [], found: false };
      try {
        const identity = await resolveToolIdentity(
          options.binding,
          options.resolveIdentity,
          context,
        );
        const target = resolveGraphTarget(identity);
        if (!target) {
          logger.warn("[zep-search] No graphUuid bound; skipping search.");
          return { facts: [], found: false };
        }

        const scope = pinned.scope ?? (hidden.has("scope") ? undefined : input.scope) ?? "edges";
        let reranker: ZepReranker | undefined =
          pinned.reranker ??
          (hidden.has("reranker") ? undefined : (input.reranker ?? "rrf"));
        const limitValue =
          pinned.limit ?? (hidden.has("limit") ? undefined : input.limit ?? 10);
        const limit = limitValue === undefined ? undefined : pageSize(limitValue);
        const mmrLambda =
          pinned.mmrLambda ?? (hidden.has("mmrLambda") ? undefined : input.mmrLambda);
        const centerNodeUuid =
          pinned.centerNodeUuid ??
          (hidden.has("centerNodeUuid") ? undefined : input.centerNodeUuid);
        if (scope === "auto") {
          if (reranker === "node_distance" || reranker === "episode_mentions") {
            if (!pinnedAutoIncompatibleReranker) {
              logger.warn(`[zep-search] Reranker '${reranker}' is not supported for auto search.`);
            }
          }
          reranker = undefined;
          const result = await options.client.graph.getContext(target.graphUuid, {
            query,
            ...(options.filters !== undefined ? { filters: options.filters } : {}),
          });
          const contextText = result.context?.trim();
          const facts = contextText ? [contextText] : [];
          return { facts, found: facts.length > 0 };
        }

        const shared = {
          query,
          ...(limit !== undefined ? { pageSize: limit } : {}),
          ...(reranker ? { reranker } : {}),
          ...(mmrLambda !== undefined ? { mmrLambda } : {}),
          ...(centerNodeUuid ? { centerNodeUuid } : {}),
          ...(options.bfsOriginNodeUuids
            ? { bfsOriginNodeUuids: options.bfsOriginNodeUuids }
            : {}),
          ...(options.filters !== undefined ? { filters: options.filters } : {}),
        };
        let facts: string[];
        switch (scope) {
          case "edges": {
            const request = shared as Parameters<typeof options.client.graph.searchEdges>[1];
            const result = await options.client.graph.searchEdges(target.graphUuid, request);
            facts = strings(result.data.map((edge) => edge.fact));
            break;
          }
          case "nodes": {
            const request = shared as Parameters<typeof options.client.graph.searchNodes>[1];
            const result = await options.client.graph.searchNodes(target.graphUuid, request);
            facts = strings(result.data.map(nameAndSummary));
            break;
          }
          case "episodes": {
            const request = shared as Parameters<typeof options.client.graph.searchEpisodes>[1];
            const result = await options.client.graph.searchEpisodes(target.graphUuid, request);
            facts = strings(result.data.map((episode) => episode.content));
            break;
          }
          case "observations": {
            const request = shared as Parameters<typeof options.client.graph.searchObservations>[1];
            const result = await options.client.graph.searchObservations(
              target.graphUuid,
              request,
            );
            facts = strings(result.data.map(nameAndSummary));
            break;
          }
          case "thread_summaries": {
            const request = shared as Parameters<
              typeof options.client.graph.searchThreadSummaries
            >[1];
            const result = await options.client.graph.searchThreadSummaries(
              target.graphUuid,
              request,
            );
            facts = strings(result.data.map(nameAndSummary));
            break;
          }
          default:
            logger.warn(`[zep-search] Unsupported search scope: ${scope}.`);
            return { facts: [], found: false };
        }
        return { facts, found: facts.length > 0 };
      } catch (error) {
        logger.warn(`[zep-search] Zep graph search failed: ${errorMessage(error)}`);
        return { facts: [], found: false };
      }
    },
  });
}
