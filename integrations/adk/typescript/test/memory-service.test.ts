import { describe, expect, it } from "vitest";
import type { Zep, ZepClient } from "@getzep/zep-cloud";
import type { BaseMemoryService, SearchMemoryRequest } from "@google/adk";
import { ZepMemoryService } from "../src/memory-service.js";
import { capturingLogger, mockZepClient, silentLogger } from "./helpers.js";

function request(overrides?: Partial<SearchMemoryRequest>): SearchMemoryRequest {
  return {
    appName: "my-app",
    userId: "alice",
    query: "where does alice live",
    ...overrides,
  };
}

describe("ZepMemoryService — searchMemory result mapping", () => {
  it("maps edges scope results to MemoryEntry[] with model authorship", async () => {
    const { client } = mockZepClient({
      searchResults: {
        edges: [{ fact: "Alice lives in Portland." }, { fact: "Alice hikes." }],
      },
    });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    const response = await service.searchMemory(request());

    expect(response.memories).toHaveLength(2);
    expect(response.memories[0]?.author).toBe("Zep");
    expect(response.memories[0]?.content.parts?.[0]?.text).toBe(
      "Alice lives in Portland.",
    );
    expect(response.memories[1]?.content.parts?.[0]?.text).toBe("Alice hikes.");
  });

  it("maps nodes scope results to MemoryEntry[] using name/summary text", async () => {
    const { client } = mockZepClient({
      searchResults: { nodes: [{ name: "Zep", summary: "A memory service." }] },
    });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      scope: "nodes",
      logger: silentLogger,
    });

    const response = await service.searchMemory(request());

    expect(response.memories).toHaveLength(1);
    expect(response.memories[0]?.content.parts?.[0]?.text).toBe(
      "Zep: A memory service.",
    );
  });

  it("maps auto scope's pre-assembled context block to a single MemoryEntry", async () => {
    const { client } = mockZepClient({
      searchResults: { context: "  assembled context block  " },
    });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      scope: "auto",
      logger: silentLogger,
    });

    const response = await service.searchMemory(request());

    expect(response.memories).toHaveLength(1);
    expect(response.memories[0]?.content.parts?.[0]?.text).toBe(
      "assembled context block",
    );
  });

  it("returns no memories when auto scope's context is empty", async () => {
    const { client } = mockZepClient({ searchResults: { context: "   " } });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      scope: "auto",
      logger: silentLogger,
    });

    const response = await service.searchMemory(request());

    expect(response.memories).toEqual([]);
  });

  it.each(["episodes", "observations", "thread_summaries"] as const)(
    "maps %s scope results to MemoryEntry[]",
    async (scope) => {
      const key =
        scope === "episodes"
          ? "episodes"
          : scope === "observations"
            ? "observations"
            : "threadSummaries";
      const item =
        scope === "episodes"
          ? { content: "raw episode text" }
          : { name: "Name", summary: "Summary" };

      const { client } = mockZepClient({ searchResults: { [key]: [item] } });
      const service = new ZepMemoryService({
        zep: client as unknown as ZepClient,
        scope,
        logger: silentLogger,
      });

      const response = await service.searchMemory(request());

      expect(response.memories).toHaveLength(1);
      const expectedText =
        scope === "episodes" ? "raw episode text" : "Name: Summary";
      expect(response.memories[0]?.content.parts?.[0]?.text).toBe(expectedText);
    },
  );
});

describe("ZepMemoryService — pass-through to graph.search", () => {
  it("passes userId, query, scope, and limit through to graph.search", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      scope: "edges",
      limit: 5,
      logger: silentLogger,
    });

    await service.searchMemory(request({ userId: "bob", query: "likes" }));

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "bob", query: "likes", scope: "edges", limit: 5 }),
    );
  });

  it("omits limit from the graph.search call when not configured", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request());

    const call = mocks.search.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(call).not.toHaveProperty("limit");
  });

  it("defaults scope to edges when not configured", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request());

    expect(mocks.search).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "edges" }),
    );
  });

  it("does not forward appName to graph.search (Zep has no app-scoped memory)", async () => {
    const { client, mocks } = mockZepClient({ searchResults: { edges: [] } });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request({ appName: "some-app" }));

    const call = mocks.search.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(call).not.toHaveProperty("appName");
  });
});

describe("ZepMemoryService — unsupported scope", () => {
  it("rejects an unsupported scope before calling graph.search, warns, and returns no memories", async () => {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const options: ConstructorParameters<typeof ZepMemoryService>[0] = {
      zep: client as unknown as ZepClient,
      // Cast needed: intentionally passing a value outside the supported enum
      // to exercise the fail-fast guard.
      scope: "unsupported_scope" as unknown as Zep.GraphSearchScope,
      logger,
    };
    const service = new ZepMemoryService(options);

    const response = await service.searchMemory(request());

    expect(response.memories).toEqual([]);
    expect(mocks.search).not.toHaveBeenCalled();
    expect(logger.warns.length).toBeGreaterThan(0);
  });
});

describe("ZepMemoryService — error handling", () => {
  it("returns empty memories and warns (without leaking query/result content) when graph.search rejects", async () => {
    const { client, mocks } = mockZepClient();
    mocks.search.mockRejectedValueOnce(new Error("boom"));
    const logger = capturingLogger();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger,
    });

    const response = await service.searchMemory(
      request({ userId: "alice", query: "some sensitive query text" }),
    );

    expect(response.memories).toEqual([]);
    expect(logger.warns.length).toBeGreaterThan(0);
    for (const warning of logger.warns) {
      expect(warning).not.toContain("some sensitive query text");
    }
  });

  it("never rejects even when graph.search throws", async () => {
    const { client, mocks } = mockZepClient();
    mocks.search.mockRejectedValueOnce(new Error("boom"));
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await expect(service.searchMemory(request())).resolves.toBeDefined();
  });
});

describe("ZepMemoryService — addSessionToMemory", () => {
  it("is a no-op that makes no Zep calls", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await expect(
      service.addSessionToMemory({ id: "session-1" } as never),
    ).resolves.toBeUndefined();

    expect(mocks.search).not.toHaveBeenCalled();
    expect(mocks.addMessages).not.toHaveBeenCalled();
    expect(mocks.create).not.toHaveBeenCalled();
    expect(mocks.userAdd).not.toHaveBeenCalled();
  });
});

describe("ZepMemoryService — satisfies BaseMemoryService", () => {
  it("type-checks as a BaseMemoryService", () => {
    const { client } = mockZepClient();
    const service: BaseMemoryService = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    expect(service).toBeInstanceOf(ZepMemoryService);
  });
});
