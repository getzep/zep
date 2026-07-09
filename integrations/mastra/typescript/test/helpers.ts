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
      addMessages: vi.fn().mockResolvedValue({ context: "ctx", messageUuids: ["u1"] }),
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
