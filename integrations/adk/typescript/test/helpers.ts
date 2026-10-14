/**
 * Shared test helpers: a fake ADK Context, a fake LlmRequest, a fake
 * LlmResponse, and a mock ZepClient.
 */

import { vi } from "vitest";
import type { Content } from "@google/genai";
import type { AdkContextLike } from "../src/identity.js";
import type { Logger } from "../src/logging.js";

/**
 * Read the `content` of the first message from the Nth `thread.addMessages`
 * call recorded on a vitest mock. Centralises the loose typing of mock call
 * args so individual tests stay strict.
 */
export function persistedContent(
  addMessages: { mock: { calls: unknown[][] } },
  callIndex = 0,
): string {
  const call = addMessages.mock.calls[callIndex];
  const payload = call?.[1] as
    | { messages?: Array<{ content?: string }> }
    | undefined;
  const content = payload?.messages?.[0]?.content;
  if (typeof content !== "string") {
    throw new Error("No persisted message content found on the mock call");
  }
  return content;
}

/** A delta-aware state stub matching the shape we read from ADK's `State`. */
export function fakeState(values: Record<string, unknown> = {}) {
  return {
    get<T>(key: string, defaultValue?: T): T | undefined {
      return key in values ? (values[key] as T) : defaultValue;
    },
  };
}

/** Build a fake ADK `Context` exposing only the fields the integration reads. */
export function fakeContext(opts: {
  userId?: string;
  sessionId?: string;
  invocationId?: string;
  userText?: string;
  userContent?: Content;
  state?: Record<string, unknown>;
}): AdkContextLike {
  const asUuid = (value: string | undefined, fallback: string) => {
    if (value === "") return "";
    return value && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
      ? value
      : fallback;
  };
  const userContent =
    opts.userContent ??
    (opts.userText !== undefined
      ? ({ role: "user", parts: [{ text: opts.userText }] } satisfies Content)
      : undefined);
  return {
    userId: asUuid(opts.userId, "123e4567-e89b-42d3-a456-426614174000"),
    sessionId: asUuid(opts.sessionId, "123e4567-e89b-42d3-a456-426614174001"),
    invocationId: opts.invocationId ?? "adk-invocation",
    userContent,
    state: fakeState(opts.state),
  };
}

/** A minimal `LlmRequest` stand-in (only the fields we touch). */
export function fakeLlmRequest(config?: Record<string, unknown>) {
  return {
    contents: [],
    liveConnectConfig: {},
    toolsDict: {},
    ...(config ? { config } : {}),
  };
}

/** Build a fake `LlmResponse` carrying assistant text and/or a function call. */
export function fakeLlmResponse(opts: {
  text?: string;
  functionCall?: boolean;
  partial?: boolean;
}) {
  const parts: Array<Record<string, unknown>> = [];
  if (opts.text !== undefined) parts.push({ text: opts.text });
  if (opts.functionCall) parts.push({ functionCall: { name: "tool", args: {} } });
  return {
    content: { role: "model", parts },
    partial: opts.partial,
  };
}

/** A no-op logger that records nothing — keeps test output clean. */
export const silentLogger: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

/**
 * A logger that captures the formatted messages passed to each level, so tests
 * can assert on warning text (e.g. that a truncation warning carries lengths
 * but never message content).
 */
export function capturingLogger(): Logger & {
  warns: string[];
  infos: string[];
} {
  const warns: string[] = [];
  const infos: string[] = [];
  return {
    warns,
    infos,
    debug: () => {},
    info: (message) => {
      infos.push(message);
    },
    warn: (message) => {
      warns.push(message);
    },
    error: () => {},
  };
}

/**
 * Build a mock `ZepClient` with `thread`, `user`, and `graph` resources whose
 * methods are vitest mocks. Cast to the concrete client type at the call site.
 */
export function mockZepClient(overrides?: {
  addMessagesContext?: string;
  searchResults?: Record<string, unknown>;
}) {
  const addMessages = vi.fn().mockResolvedValue({
    context: overrides?.addMessagesContext,
    messageUuids: ["uuid-1"],
    taskId: "task-1",
  });
  const createThread = vi
    .fn()
    .mockResolvedValue({ uuid: "thread-uuid", graphUuid: "graph-uuid" });
  const createUser = vi
    .fn()
    .mockResolvedValue({ uuid: "user-uuid", graphUuid: "graph-uuid" });
  const getUser = vi.fn().mockImplementation(async (userUuid: string) => ({
    uuid: userUuid,
    graphUuid: "graph-uuid",
  }));
  const searchEdges = vi.fn().mockResolvedValue({
    data: overrides?.searchResults?.edges ?? [],
  });
  const searchNodes = vi.fn().mockResolvedValue({
    data: overrides?.searchResults?.nodes ?? [],
  });
  const searchEpisodes = vi.fn().mockResolvedValue({
    data: overrides?.searchResults?.episodes ?? [],
  });
  const searchObservations = vi.fn().mockResolvedValue({
    data: overrides?.searchResults?.observations ?? [],
  });
  const searchThreadSummaries = vi.fn().mockResolvedValue({
    data: overrides?.searchResults?.threadSummaries ?? [],
  });
  const getContext = vi.fn().mockResolvedValue({
    context: overrides?.searchResults?.context,
  });
  const deleteUser = vi.fn().mockResolvedValue(undefined);
  const deleteThread = vi.fn().mockResolvedValue(undefined);

  return {
    client: {
      thread: { addMessages, create: createThread, delete: deleteThread },
      user: { create: createUser, get: getUser, delete: deleteUser },
      graph: {
        searchEdges,
        searchNodes,
        searchEpisodes,
        searchObservations,
        searchThreadSummaries,
        getContext,
      },
    },
    mocks: {
      addMessages,
      createThread,
      create: createThread,
      createUser,
      userAdd: createUser,
      getUser,
      deleteUser,
      deleteThread,
      searchEdges,
      searchNodes,
      searchEpisodes,
      searchObservations,
      searchThreadSummaries,
      getContext,
    },
  };
}
