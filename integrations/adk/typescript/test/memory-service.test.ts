import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { BaseMemoryService, SearchMemoryRequest } from "@google/adk";
import { ZepMemoryService } from "../src/memory-service.js";
import type { ZepGraphSearchScope } from "../src/graph-search-tool.js";
import {
  capturingLogger,
  mockZepClient,
  silentLogger,
  MOCK_GRAPH_UUID,
  MOCK_USER_UUID,
} from "./helpers.js";

const GRAPH_UUID = "44444444-4444-4444-4444-444444444444";

function request(overrides?: Partial<SearchMemoryRequest>): SearchMemoryRequest {
  return {
    appName: "my-app",
    userId: MOCK_USER_UUID,
    query: "where does alice live",
    ...overrides,
  };
}

describe("ZepMemoryService — searchMemory result mapping", () => {
  it("maps edges scope results to MemoryEntry[] with model authorship", async () => {
    const { client } = mockZepClient({
      searchData: [
        { fact: "Alice lives in Portland." },
        { fact: "Alice hikes." },
      ],
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
      searchData: [{ name: "Zep", summary: "A memory service." }],
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
    const { client, mocks } = mockZepClient({
      autoContext: "  assembled context block  ",
    });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      scope: "auto",
      logger: silentLogger,
    });

    const response = await service.searchMemory(request());

    expect(mocks.getContext).toHaveBeenCalledWith(MOCK_GRAPH_UUID, {
      query: "where does alice live",
      filters: undefined,
    });
    expect(response.memories).toHaveLength(1);
    expect(response.memories[0]?.content.parts?.[0]?.text).toBe(
      "assembled context block",
    );
  });

  it("returns no memories when auto scope's context is empty", async () => {
    const { client } = mockZepClient({ autoContext: "   " });
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
      const item =
        scope === "episodes"
          ? { content: "raw episode text" }
          : scope === "observations"
            ? { name: "Name", summary: "Summary" }
            : { summary: "Summary" };

      const { client } = mockZepClient({ searchData: [item] });
      const service = new ZepMemoryService({
        zep: client as unknown as ZepClient,
        scope,
        logger: silentLogger,
      });

      const response = await service.searchMemory(request());

      expect(response.memories).toHaveLength(1);
      const expectedText =
        scope === "episodes"
          ? "raw episode text"
          : scope === "observations"
            ? "Name: Summary"
            : "Summary";
      expect(response.memories[0]?.content.parts?.[0]?.text).toBe(expectedText);
    },
  );
});

describe("ZepMemoryService — graph addressing", () => {
  it("reads the graph UUID of the user named by the request", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      scope: "edges",
      limit: 5,
      logger: silentLogger,
    });

    await service.searchMemory(request({ query: "likes" }));

    expect(mocks.userGet).toHaveBeenCalledWith(MOCK_USER_UUID);
    expect(mocks.searchEdges).toHaveBeenCalledWith(MOCK_GRAPH_UUID, {
      limit: 5,
      body: { query: "likes" },
    });
  });

  it("reads the graph UUID of a user only once", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request());
    await service.searchMemory(request());

    expect(mocks.userGet).toHaveBeenCalledTimes(1);
    expect(mocks.searchEdges).toHaveBeenCalledTimes(2);
  });

  it("uses a configured graphUuid and never reads the user", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });

    await service.searchMemory(request());

    expect(mocks.userGet).not.toHaveBeenCalled();
    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ body: { query: "where does alice live" } }),
    );
  });

  it("omits limit from the search call when not configured", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request());

    const call = mocks.searchEdges.mock.calls[0]?.[1] as Record<
      string,
      unknown
    >;
    expect(call.limit).toBeUndefined();
  });

  it("defaults scope to edges when not configured", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request());

    expect(mocks.searchEdges).toHaveBeenCalled();
    expect(mocks.searchNodes).not.toHaveBeenCalled();
  });

  it("does not forward appName to Zep (Zep has no app-scoped memory)", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });

    await service.searchMemory(request({ appName: "some-app" }));

    const call = mocks.searchEdges.mock.calls[0]?.[1] as {
      body: Record<string, unknown>;
    };
    expect(call.body).not.toHaveProperty("appName");
  });

  it("returns no memories when the user has no graph UUID", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userGet.mockResolvedValueOnce({ uuid: MOCK_USER_UUID });
    const logger = capturingLogger();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger,
    });

    const response = await service.searchMemory(request());

    expect(response.memories).toEqual([]);
    expect(mocks.searchEdges).not.toHaveBeenCalled();
    expect(logger.warns.length).toBeGreaterThan(0);
  });
});

describe("ZepMemoryService — unsupported scope", () => {
  it("rejects an unsupported scope before it searches, warns, and returns no memories", async () => {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const options: ConstructorParameters<typeof ZepMemoryService>[0] = {
      zep: client as unknown as ZepClient,
      // Cast needed: intentionally passing a value outside the supported enum
      // to exercise the fail-fast guard.
      scope: "unsupported_scope" as unknown as ZepGraphSearchScope,
      logger,
    };
    const service = new ZepMemoryService(options);

    const response = await service.searchMemory(request());

    expect(response.memories).toEqual([]);
    expect(mocks.searchEdges).not.toHaveBeenCalled();
    expect(mocks.userGet).not.toHaveBeenCalled();
    expect(logger.warns.length).toBeGreaterThan(0);
  });
});

describe("ZepMemoryService — error handling", () => {
  it("returns empty memories and warns (without leaking query/result content) when the search rejects", async () => {
    const { client, mocks } = mockZepClient();
    mocks.searchEdges.mockRejectedValueOnce(new Error("boom"));
    const logger = capturingLogger();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger,
    });

    const response = await service.searchMemory(
      request({ query: "some sensitive query text" }),
    );

    expect(response.memories).toEqual([]);
    expect(logger.warns.length).toBeGreaterThan(0);
    for (const warning of logger.warns) {
      expect(warning).not.toContain("some sensitive query text");
    }
  });

  it("never rejects even when the search throws", async () => {
    const { client, mocks } = mockZepClient();
    mocks.searchEdges.mockRejectedValueOnce(new Error("boom"));
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

    expect(mocks.searchEdges).not.toHaveBeenCalled();
    expect(mocks.addMessages).not.toHaveBeenCalled();
    expect(mocks.threadCreate).not.toHaveBeenCalled();
    expect(mocks.userCreate).not.toHaveBeenCalled();
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
