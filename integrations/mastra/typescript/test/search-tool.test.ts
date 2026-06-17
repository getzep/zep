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
    });
  });

  it("honors a pinned scope, limit, and graphId binding", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({
      nodes: [{ name: "Apollo", summary: "A spacecraft program" }],
    });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { graphId: "kb-1" },
      scope: "nodes",
      limit: 5,
    });

    const result = await run(tool, { query: "Apollo" });

    expect(result.facts).toEqual(["Apollo: A spacecraft program"]);
    expect(zep.graph.search).toHaveBeenCalledWith({
      graphId: "kb-1",
      query: "Apollo",
      scope: "nodes",
      limit: 5,
    });
  });

  it("returns the materialized context string for auto scope", async () => {
    const zep = makeFakeZep();
    zep.graph.search.mockResolvedValueOnce({ context: "  assembled block  " });
    const tool = createZepSearchTool({
      client: asZep(zep),
      binding: { userId: "u1" },
      scope: "auto",
    });
    const result = await run(tool, { query: "anything" });
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
      scope: "thread_summaries",
    });

    const result = await run(tool, { query: "what happened" });

    expect(result.facts).toEqual(["Onboarding: User set up their account", "NoSummary"]);
    expect(result.found).toBe(true);
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "what happened",
      scope: "thread_summaries",
    });
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
      scope: "observations",
    });

    const result = await run(tool, { query: "habits" });

    expect(result.facts).toEqual(["Pattern A: User logs in every morning", "Pattern B"]);
    expect(result.found).toBe(true);
    expect(zep.graph.search).toHaveBeenCalledWith({
      userId: "u1",
      query: "habits",
      scope: "observations",
    });
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
});
