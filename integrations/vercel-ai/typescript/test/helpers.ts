import { vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";

/**
 * A minimal in-memory fake of the Zep client surface the integration uses.
 * Each method is a vitest mock so tests can assert calls and override returns.
 */
export interface FakeZep {
  thread: {
    addMessages: ReturnType<typeof vi.fn>;
    getUserContext: ReturnType<typeof vi.fn>;
    create: ReturnType<typeof vi.fn>;
  };
  graph: {
    add: ReturnType<typeof vi.fn>;
    search: ReturnType<typeof vi.fn>;
  };
  user: {
    add: ReturnType<typeof vi.fn>;
  };
}

/** Build a fresh fake Zep client with sensible default resolved values. */
export function makeFakeZep(): FakeZep {
  return {
    thread: {
      addMessages: vi.fn().mockResolvedValue({ context: "ctx" }),
      getUserContext: vi.fn().mockResolvedValue({ context: "USER CONTEXT BLOCK" }),
      create: vi.fn().mockResolvedValue({ uuid: "t1" }),
    },
    graph: {
      add: vi.fn().mockResolvedValue({ uuid: "ep1" }),
      search: vi.fn().mockResolvedValue({ edges: [], nodes: [], episodes: [] }),
    },
    user: {
      add: vi.fn().mockResolvedValue({ userId: "u" }),
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
