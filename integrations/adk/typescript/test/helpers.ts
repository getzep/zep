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
  const userContent =
    opts.userContent ??
    (opts.userText !== undefined
      ? ({ role: "user", parts: [{ text: opts.userText }] } satisfies Content)
      : undefined);
  return {
    userId: opts.userId ?? "adk-user",
    sessionId: opts.sessionId ?? "adk-session",
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

/** UUID of the user that {@link mockZepClient} returns by default. */
export const MOCK_USER_UUID = "11111111-1111-1111-1111-111111111111";
/** UUID of the graph that {@link mockZepClient} returns by default. */
export const MOCK_GRAPH_UUID = "22222222-2222-2222-2222-222222222222";
/** UUID of the thread that {@link mockZepClient} returns by default. */
export const MOCK_THREAD_UUID = "33333333-3333-3333-3333-333333333333";

/** One page of v4 search results, with the fields the integration reads. */
function fakePage(data: unknown[]) {
  return { data, hasNextPage: () => false };
}

/**
 * Build a mock `ZepClient` with `thread`, `user`, and `graph` resources whose
 * methods are vitest mocks. Cast to the concrete client type at the call site.
 *
 * The graph mocks follow Zep v4: one search method for each scope, each one
 * returning a page, and `graph.getContext` for the `"auto"` scope.
 */
export function mockZepClient(overrides?: {
  addMessagesContext?: string;
  searchData?: unknown[];
  autoContext?: string;
}) {
  const addMessages = vi.fn().mockResolvedValue({
    context: overrides?.addMessagesContext,
    messages: [{ uuid: "uuid-1" }],
  });
  const threadCreate = vi.fn().mockResolvedValue({
    uuid: MOCK_THREAD_UUID,
    userUuid: MOCK_USER_UUID,
    graphUuid: MOCK_GRAPH_UUID,
  });
  const userCreate = vi.fn().mockResolvedValue({
    uuid: MOCK_USER_UUID,
    graphUuid: MOCK_GRAPH_UUID,
  });
  const userGet = vi.fn().mockResolvedValue({
    uuid: MOCK_USER_UUID,
    graphUuid: MOCK_GRAPH_UUID,
  });

  const page = () => fakePage(overrides?.searchData ?? []);
  const searchEdges = vi.fn().mockImplementation(async () => page());
  const searchNodes = vi.fn().mockImplementation(async () => page());
  const searchEpisodes = vi.fn().mockImplementation(async () => page());
  const searchObservations = vi.fn().mockImplementation(async () => page());
  const searchThreadSummaries = vi.fn().mockImplementation(async () => page());
  const getContext = vi
    .fn()
    .mockResolvedValue({ context: overrides?.autoContext });

  return {
    client: {
      thread: { addMessages, create: threadCreate },
      user: { create: userCreate, get: userGet },
      graph: {
        getContext,
        searchEdges,
        searchNodes,
        searchEpisodes,
        searchObservations,
        searchThreadSummaries,
      },
    },
    mocks: {
      addMessages,
      threadCreate,
      userCreate,
      userGet,
      getContext,
      searchEdges,
      searchNodes,
      searchEpisodes,
      searchObservations,
      searchThreadSummaries,
    },
  };
}
