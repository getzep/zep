import { vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";

/**
 * A minimal in-memory fake of the Zep client surface the integration uses.
 * Each method is a vitest mock so tests can assert calls and override returns.
 */
export interface FakeZep {
  thread: {
    addMessages: ReturnType<typeof vi.fn>;
    getContext: ReturnType<typeof vi.fn>;
    create: ReturnType<typeof vi.fn>;
  };
  graph: {
    episode: {
      add: ReturnType<typeof vi.fn>;
    };
    getContext: ReturnType<typeof vi.fn>;
    searchEdges: ReturnType<typeof vi.fn>;
    searchNodes: ReturnType<typeof vi.fn>;
    searchEpisodes: ReturnType<typeof vi.fn>;
    searchObservations: ReturnType<typeof vi.fn>;
    searchThreadSummaries: ReturnType<typeof vi.fn>;
  };
  user: {
    create: ReturnType<typeof vi.fn>;
  };
}

/** The UUID of the fake Zep user. */
export const USER_UUID = "11111111-1111-4111-8111-111111111111";
/** The UUID of the fake user's graph. */
export const GRAPH_UUID = "22222222-2222-4222-8222-222222222222";
/** The UUID of the fake Zep thread. */
export const THREAD_UUID = "33333333-3333-4333-8333-333333333333";

/** Build a fresh fake Zep client with sensible default resolved values. */
export function makeFakeZep(): FakeZep {
  return {
    thread: {
      addMessages: vi.fn().mockResolvedValue({ context: "ctx" }),
      getContext: vi.fn().mockResolvedValue({ context: "USER CONTEXT BLOCK" }),
      create: vi.fn().mockResolvedValue({ uuid: THREAD_UUID, userUuid: USER_UUID }),
    },
    graph: {
      episode: {
        add: vi.fn().mockResolvedValue({ uuid: "ep1" }),
      },
      getContext: vi.fn().mockResolvedValue({ context: "AUTO CONTEXT BLOCK" }),
      searchEdges: vi.fn().mockResolvedValue({ data: [] }),
      searchNodes: vi.fn().mockResolvedValue({ data: [] }),
      searchEpisodes: vi.fn().mockResolvedValue({ data: [] }),
      searchObservations: vi.fn().mockResolvedValue({ data: [] }),
      searchThreadSummaries: vi.fn().mockResolvedValue({ data: [] }),
    },
    user: {
      create: vi
        .fn()
        .mockResolvedValue({ uuid: USER_UUID, graphUuid: GRAPH_UUID }),
    },
  };
}

/** Cast a fake to the `ZepClient` type for passing into the integration. */
export function asZep(fake: FakeZep): ZepClient {
  return fake as unknown as ZepClient;
}

/**
 * An AI SDK tool with a callable `execute`. We type `execute` deliberately
 * loosely here: the AI SDK's `Tool` type is generic and heavy, and inferring
 * its exact input/output through `ReturnType` blows up the type-checker. For
 * mock tests we only need to call `execute` and inspect the resolved object.
 */
interface ExecutableTool {
  execute?: (...args: never[]) => unknown;
}

/**
 * Invoke a tool's `execute` and return its (awaited) structured output.
 *
 * Returns a permissive record so tests can read whichever fields the tool
 * produces (`facts`, `found`, `stored`, `message`, `context`) without threading
 * the AI SDK's heavy generic `Tool` type through the assertions.
 */
export async function run<T extends Record<string, unknown> = Record<string, unknown>>(
  tool: ExecutableTool,
  input: Record<string, unknown>,
): Promise<T> {
  if (!tool.execute) throw new Error("tool has no execute");
  const exec = tool.execute as (input: unknown, options: unknown) => unknown;
  const result = await exec(input, undefined);
  if (result === undefined || result === null || typeof result !== "object") {
    throw new Error(`expected a structured tool result, got: ${typeof result}`);
  }
  return result as T;
}
