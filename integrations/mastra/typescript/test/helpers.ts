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
    delete: ReturnType<typeof vi.fn>;
  };
  graph: {
    episode: { add: ReturnType<typeof vi.fn> };
    getContext: ReturnType<typeof vi.fn>;
    searchEdges: ReturnType<typeof vi.fn>;
    searchNodes: ReturnType<typeof vi.fn>;
    searchEpisodes: ReturnType<typeof vi.fn>;
    searchObservations: ReturnType<typeof vi.fn>;
    searchThreadSummaries: ReturnType<typeof vi.fn>;
  };
  user: {
    create: ReturnType<typeof vi.fn>;
    delete: ReturnType<typeof vi.fn>;
  };
}

/** Build a fresh fake Zep client with sensible default resolved values. */
export function makeFakeZep(): FakeZep {
  return {
    thread: {
      addMessages: vi.fn().mockResolvedValue({ context: "ctx", messageUuids: ["u1"] }),
      getContext: vi.fn().mockResolvedValue({ context: "USER CONTEXT BLOCK" }),
      create: vi.fn().mockResolvedValue({ uuid: "t1" }),
      delete: vi.fn().mockResolvedValue({}),
    },
    graph: {
      episode: { add: vi.fn().mockResolvedValue({ uuid: "ep1" }) },
      getContext: vi.fn().mockResolvedValue({ context: "GRAPH CONTEXT BLOCK" }),
      searchEdges: vi.fn().mockResolvedValue({ data: [] }),
      searchNodes: vi.fn().mockResolvedValue({ data: [] }),
      searchEpisodes: vi.fn().mockResolvedValue({ data: [] }),
      searchObservations: vi.fn().mockResolvedValue({ data: [] }),
      searchThreadSummaries: vi.fn().mockResolvedValue({ data: [] }),
    },
    user: {
      create: vi.fn().mockResolvedValue({ uuid: "u1", graphUuid: "g1" }),
      delete: vi.fn().mockResolvedValue({}),
    },
  };
}

/** Cast a fake to the `ZepClient` type for passing into the integration. */
export function asZep(fake: FakeZep): ZepClient {
  return fake as unknown as ZepClient;
}

/** A Mastra tool with a callable `execute`, as returned by the `create*` helpers. */
interface ExecutableTool<TIn, TResult> {
  execute?: (input: TIn, context: never) => Promise<TResult>;
}

/** The structured output of a tool: its execute result minus `void`/validation errors. */
type ToolOutput<TResult> = TResult extends object
  ? TResult extends { validationErrors: unknown }
    ? never
    : TResult
  : never;

/**
 * Invoke a tool's `execute` and return its structured output.
 *
 * Mastra types `execute` as `Promise<TSchemaOut | ValidationError | void>`. Our
 * tools always resolve to the structured output, so this helper narrows that
 * union down to the success shape (asserting a non-void object) to keep tests
 * readable and typed.
 *
 * @param context - Optional second argument forwarded to `execute` (e.g.
 *   `{ requestContext }`), for exercising per-call identity resolution.
 *   Defaults to `undefined`, matching a tool invoked with no execution
 *   context.
 */
export async function run<TIn, TResult>(
  tool: ExecutableTool<TIn, TResult>,
  input: TIn,
  context?: unknown,
): Promise<ToolOutput<TResult>> {
  if (!tool.execute) throw new Error("tool has no execute");
  const result = await tool.execute(input, context as never);
  if (result === undefined || result === null || typeof result !== "object") {
    throw new Error(`expected a structured tool result, got: ${typeof result}`);
  }
  return result as ToolOutput<TResult>;
}
