import { describe, expect, it } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import type { BaseMemoryService, SearchMemoryRequest } from "@google/adk";
import { ZepMemoryService } from "../src/memory-service.js";
import { capturingLogger, mockZepClient, silentLogger } from "./helpers.js";

const USER_UUID = "123e4567-e89b-42d3-a456-426614174000";
const GRAPH_UUID = "123e4567-e89b-42d3-a456-426614174002";

function request(overrides?: Partial<SearchMemoryRequest>): SearchMemoryRequest {
  return {
    appName: "my-app",
    userId: USER_UUID,
    query: "where does the user live",
    ...overrides,
  };
}

describe("ZepMemoryService", () => {
  it("maps search results to ADK memory entries", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: { edges: [{ fact: "The user lives in Portland." }] },
    });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "edges",
      limit: 5,
      logger: silentLogger,
    });

    const response = await service.searchMemory(request());
    expect(response.memories[0]?.author).toBe("Zep");
    expect(response.memories[0]?.content.parts?.[0]?.text).toBe(
      "The user lives in Portland.",
    );
    expect(mocks.searchEdges).toHaveBeenCalledWith(
      GRAPH_UUID,
      expect.objectContaining({ query: request().query, pageSize: 5 }),
    );
  });

  it.each([
    ["nodes", "searchNodes"],
    ["episodes", "searchEpisodes"],
    ["observations", "searchObservations"],
    ["thread_summaries", "searchThreadSummaries"],
  ] as const)("supports the %s scope", async (scope, method) => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope,
      logger: silentLogger,
    });
    await service.searchMemory(request());
    expect(mocks[method]).toHaveBeenCalled();
  });

  it("maps auto scope to one context memory", async () => {
    const { client, mocks } = mockZepClient({
      searchResults: { context: "Relevant context." },
    });
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      scope: "auto",
      logger: silentLogger,
    });
    const response = await service.searchMemory(request());
    expect(response.memories).toHaveLength(1);
    expect(response.memories[0]?.content.parts?.[0]?.text).toBe(
      "Relevant context.",
    );
    expect(mocks.getContext).toHaveBeenCalled();
  });

  it("resolves and caches a graph for the ADK user UUID", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    await service.searchMemory(request());
    await service.searchMemory(request());
    expect(mocks.getUser).toHaveBeenCalledTimes(1);
    expect(mocks.getUser).toHaveBeenCalledWith(USER_UUID);
  });

  it("returns no memories when the user ID is not a Zep UUID", async () => {
    const { client, mocks } = mockZepClient();
    const logger = capturingLogger();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger,
    });
    const response = await service.searchMemory(request({ userId: "adk-user" }));
    expect(response.memories).toEqual([]);
    expect(mocks.searchEdges).not.toHaveBeenCalled();
    expect(logger.warns.length).toBeGreaterThan(0);
  });

  it("does not raise when the v4 search call fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.searchEdges.mockRejectedValueOnce(new Error("search failed"));
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      graphUuid: GRAPH_UUID,
      logger: silentLogger,
    });
    await expect(service.searchMemory(request())).resolves.toEqual({
      memories: [],
    });
  });

  it("keeps addSessionToMemory as a no-op", async () => {
    const { client, mocks } = mockZepClient();
    const service = new ZepMemoryService({
      zep: client as unknown as ZepClient,
      logger: silentLogger,
    });
    await service.addSessionToMemory({ id: "session" } as never);
    expect(mocks.addMessages).not.toHaveBeenCalled();
    expect(mocks.createUser).not.toHaveBeenCalled();
    expect(mocks.createThread).not.toHaveBeenCalled();
  });

  it("implements the ADK BaseMemoryService interface", () => {
    const { client } = mockZepClient();
    expect(
      new ZepMemoryService({
        zep: client as unknown as ZepClient,
        logger: silentLogger,
      }) satisfies BaseMemoryService,
    ).toBeInstanceOf(ZepMemoryService);
  });
});
