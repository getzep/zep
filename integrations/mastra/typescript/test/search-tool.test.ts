import { describe, it, expect, vi } from "vitest";
import { createZepSearchTool } from "../src/index.js";
import { makeFakeZep, asZep, run } from "./helpers.js";

describe("createZepSearchTool", () => {
  it("searches edges by default and returns facts", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({
      edges: [{ fact: "Jane lives in Portland" }, { fact: "Jane is an engineer" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
    });

    const result = await run(tool, { query: "where does Jane live" });

    expect(result).toEqual({
      facts: ["Jane lives in Portland", "Jane is an engineer"],
      found: true,
    });
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "where does Jane live",
      scope: "edges",
      reranker: "rrf",
      limit: 10,
    });
  });

  it("returns the materialized context string for auto scope", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ context: "  assembled block  " });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
    });
    const result = await run(tool, { query: "anything", scope: "auto" });
    expect(result.facts).toEqual(["assembled block"]);
    expect(result.found).toBe(true);
  });

  it("returns formatted entries for the thread_summaries scope", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({
      threadSummaries: [
        { name: "Onboarding", summary: "User set up their account" },
        { name: "NoSummary" },
      ],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
    });

    const result = await run(tool, { query: "what happened", scope: "thread_summaries" });

    expect(result.facts).toEqual(["Onboarding: User set up their account", "NoSummary"]);
    expect(result.found).toBe(true);
  });

  it("returns formatted entries for the observations scope", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({
      observations: [
        { name: "Pattern A", summary: "User logs in every morning" },
        { name: "Pattern B" },
      ],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
    });

    const result = await run(tool, { query: "habits", scope: "observations" });

    expect(result.facts).toEqual(["Pattern A: User logs in every morning", "Pattern B"]);
    expect(result.found).toBe(true);
  });

  it("returns found: false with no results", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ edges: [] });
    const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
    const result = await run(tool, { query: "nothing here" });
    expect(result).toEqual({ facts: [], found: false });
  });

  it("never throws when Zep fails", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockRejectedValueOnce(new Error("timeout"));
    const warn = vi.fn();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      logger: { warn },
    });
    const result = await run(tool, { query: "x" });
    expect(result).toEqual({ facts: [], found: false });
    expect(warn).toHaveBeenCalledOnce();
  });

  it("returns empty when nothing is bound", async () => {
    const zep = makeFakeZep();
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: {},
      logger: { warn: vi.fn() },
    });
    const result = await run(tool, { query: "x" });
    expect(result.found).toBe(false);
    expect(zep.graph.search).not.toHaveBeenCalled();
  });

  it("resolves identity from requestContext when resolveIdentity is provided", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ edges: [{ fact: "override fact" }] });
    const resolveIdentity = vi.fn().mockReturnValue({ userId: "u2" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      resolveIdentity,
    });

    const result = await run(tool, { query: "q" }, { requestContext: { tenant: "acme" } });

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(result.facts).toEqual(["override fact"]);
    expect(zep.graph.search).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "u2" }),
    );
  });

  it("awaits an async resolveIdentity and uses the resolved identity", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ edges: [{ fact: "async fact" }] });
    const resolveIdentity = vi.fn().mockResolvedValue({ userId: "u2" });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      resolveIdentity,
    });

    const result = await run(tool, { query: "q" }, { requestContext: { tenant: "acme" } });

    expect(resolveIdentity).toHaveBeenCalledWith({ tenant: "acme" });
    expect(result.facts).toEqual(["async fact"]);
    expect(zep.graph.search).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "u2" }),
    );
  });

  it("falls back to constructor binding when resolveIdentity is unset or returns nothing", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ edges: [{ fact: "base fact" }] });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
    });

    const result = await run(tool, { query: "q" }, { requestContext: {} });

    expect(result.facts).toEqual(["base fact"]);
    expect(zep.graph.search).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "u1" }),
    );
  });

  describe("pin-or-expose", () => {
    it("exposes scope/reranker/limit/mmrLambda/centerNodeUuid by default", () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(Object.keys(shape).sort()).toEqual(
        ["centerNodeUuid", "limit", "mmrLambda", "query", "reranker", "scope"].sort(),
      );
    });

    it("supports all six GraphSearchScope values in the exposed enum", () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
      const shape = (
        tool.inputSchema as unknown as {
          shape: { scope: { unwrap(): { options: string[] } } };
        }
      ).shape;
      expect(shape.scope.unwrap().options.sort()).toEqual(
        ["auto", "edges", "episodes", "nodes", "observations", "thread_summaries"].sort(),
      );
    });

    it("supports all five Reranker values in the exposed enum", () => {
      const zep = makeFakeZep();
      const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });
      const shape = (
        tool.inputSchema as unknown as {
          shape: { reranker: { unwrap(): { options: string[] } } };
        }
      ).shape;
      expect(shape.reranker.unwrap().options.sort()).toEqual(
        ["cross_encoder", "episode_mentions", "mmr", "node_distance", "rrf"].sort(),
      );
    });

    it("pinned params are hidden from the schema and always sent to Zep", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ nodes: [{ name: "Apollo" }] });
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        pinnedParams: { scope: "nodes", limit: 3 },
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.scope).toBeUndefined();
      expect(shape.limit).toBeUndefined();

      // Model attempts to override a pinned param — pinned value always wins.
      const result = await run(tool, { query: "q", scope: "edges", limit: 99 } as never);
      expect(result.facts).toEqual(["Apollo"]);
      expect(zep.graph.search).toHaveBeenCalledWith(
        expect.objectContaining({ scope: "nodes", limit: 3 }),
      );
    });

    it("hidden params are omitted from the schema AND from the Zep call", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ edges: [] });
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        hiddenParams: new Set(["mmrLambda", "centerNodeUuid"]),
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.mmrLambda).toBeUndefined();
      expect(shape.centerNodeUuid).toBeUndefined();

      await run(tool, { query: "q" });
      const sentParams = zep.graph.search.mock.calls[0]![0] as Record<string, unknown>;
      expect(sentParams).not.toHaveProperty("mmrLambda");
      expect(sentParams).not.toHaveProperty("centerNodeUuid");
    });

    it("omits unset optional params from the Zep call (query-only call)", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ edges: [] });
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        hiddenParams: new Set(["scope", "reranker", "limit"]),
      });

      await run(tool, { query: "q" });
      const sentParams = zep.graph.search.mock.calls[0]![0] as Record<string, unknown>;
      expect(sentParams).toEqual({ userId: "u1", query: "q" });
    });

    it("legacy scope/limit/reranker constructor args pin (and hide) their parameter", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ episodes: [{ content: "raw text" }] });
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        scope: "episodes",
        limit: 5,
        reranker: "mmr",
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.scope).toBeUndefined();
      expect(shape.limit).toBeUndefined();
      expect(shape.reranker).toBeUndefined();

      const result = await run(tool, { query: "q" } as never);
      expect(result.facts).toEqual(["raw text"]);
      expect(zep.graph.search).toHaveBeenCalledWith({
        userId: "u1",
        query: "q",
        scope: "episodes",
        limit: 5,
        reranker: "mmr",
      });
    });

    it("searchFilters and bfsOriginNodeUuids are constructor-only and always applied", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ edges: [] });
      const searchFilters = { nodeLabels: ["Person"] };
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        searchFilters,
        bfsOriginNodeUuids: ["uuid-1", "uuid-2"],
      });

      const shape = (tool.inputSchema as unknown as { shape: Record<string, unknown> }).shape;
      expect(shape.searchFilters).toBeUndefined();
      expect(shape.bfsOriginNodeUuids).toBeUndefined();

      await run(tool, { query: "q" });
      expect(zep.graph.search).toHaveBeenCalledWith(
        expect.objectContaining({
          searchFilters,
          bfsOriginNodeUuids: ["uuid-1", "uuid-2"],
        }),
      );
    });

    it("omits a model-provided auto-incompatible reranker when scope is 'auto'", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ context: "block" });
      const warn = vi.fn();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        logger: { warn },
      });

      await run(tool, { query: "q", scope: "auto", reranker: "node_distance" });

      const sentParams = zep.graph.search.mock.calls[0]![0] as Record<string, unknown>;
      expect(sentParams).toMatchObject({ scope: "auto" });
      expect(sentParams).not.toHaveProperty("reranker");
      expect(warn).toHaveBeenCalledOnce();
    });

    it("resolves a pinned auto-incompatible scope/reranker pair at construction", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ context: "block" });
      const warn = vi.fn();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        pinnedParams: { scope: "auto", reranker: "episode_mentions" },
        logger: { warn },
      });
      expect(warn).toHaveBeenCalledOnce();

      await run(tool, { query: "q" });

      const sentParams = zep.graph.search.mock.calls[0]![0] as Record<string, unknown>;
      expect(sentParams).toMatchObject({ scope: "auto" });
      expect(sentParams).not.toHaveProperty("reranker");
    });

    it("clamps a model-provided limit to Zep's 50-result ceiling", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ edges: [] });
      const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });

      await run(tool, { query: "q", limit: 200 });

      expect(zep.graph.search).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 50 }),
      );
    });

    it("clamps a pinned limit to Zep's 50-result ceiling at construction, with a warning", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ edges: [] });
      const warn = vi.fn();
      const tool = createZepSearchTool({
        client: asZep(zep),
        binding: { userId: "u1" },
        pinnedParams: { limit: 200 },
        logger: { warn },
      });
      expect(warn).toHaveBeenCalledOnce();

      await run(tool, { query: "q" });

      expect(zep.graph.search).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 50 }),
      );
    });

    it("model-provided scope/reranker/limit/mmrLambda/centerNodeUuid are forwarded when exposed", async () => {
      const zep = makeFakeZep();
      zep.graph.search.mockResolvedValueOnce({ nodes: [{ name: "Apollo" }] });
      const tool = createZepSearchTool({ client: asZep(zep), binding: { userId: "u1" } });

      await run(tool, {
        query: "q",
        scope: "nodes",
        reranker: "mmr",
        limit: 7,
        mmrLambda: 0.5,
        centerNodeUuid: "uuid-9",
      });

      expect(zep.graph.search).toHaveBeenCalledWith({
        userId: "u1",
        query: "q",
        scope: "nodes",
        reranker: "mmr",
        limit: 7,
        mmrLambda: 0.5,
        centerNodeUuid: "uuid-9",
      });
    });
  });
});
