/**
 * Shared test helpers: a fake ADK Context, a fake LlmRequest, a fake
 * LlmResponse, and a mock ZepClient.
 */

import { vi } from "vitest";
import type { Content } from "@google/genai";
import type { AdkContextLike } from "../src/identity.js";
import type { Logger } from "../src/logging.js";

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
  const create = vi.fn().mockResolvedValue({ threadId: "t", userId: "u" });
  const userAdd = vi.fn().mockResolvedValue({ userId: "u" });
  const search = vi
    .fn()
    .mockResolvedValue(overrides?.searchResults ?? { edges: [] });

  return {
    client: {
      thread: { addMessages, create },
      user: { add: userAdd },
      graph: { search },
    },
    mocks: { addMessages, create, userAdd, search },
  };
}
