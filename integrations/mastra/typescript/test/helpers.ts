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

/**
 * A Zep v4 search method returns a page object. The integration reads only
 * the `data` array, so the fake page carries that field alone.
 */
export function page<T>(data: T[]): { data: T[] } {
  return { data };
}

/** Build a fresh fake Zep client with sensible default resolved values. */
export function makeFakeZep(): FakeZep {
  return {
    thread: {
      addMessages: vi.fn().mockResolvedValue({ context: "ctx", messageUuids: ["u1"] }),
      getContext: vi.fn().mockResolvedValue({ context: "USER CONTEXT BLOCK" }),
      create: vi.fn().mockResolvedValue({ uuid: "t1", userUuid: "u1", graphUuid: "g1" }),
    },
    graph: {
      episode: {
        add: vi.fn().mockResolvedValue({ uuid: "ep1" }),
      },
      getContext: vi.fn().mockResolvedValue({ context: "" }),
      searchEdges: vi.fn().mockResolvedValue(page([])),
      searchNodes: vi.fn().mockResolvedValue(page([])),
      searchEpisodes: vi.fn().mockResolvedValue(page([])),
      searchObservations: vi.fn().mockResolvedValue(page([])),
      searchThreadSummaries: vi.fn().mockResolvedValue(page([])),
    },
    user: {
      create: vi.fn().mockResolvedValue({ uuid: "u1", graphUuid: "g1" }),
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
