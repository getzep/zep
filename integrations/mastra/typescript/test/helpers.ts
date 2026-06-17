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
