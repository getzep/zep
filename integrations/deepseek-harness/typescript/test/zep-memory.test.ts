import { Context } from "@deepseek-ai/cordis";
import { agentEvents } from "@deepseek-ai/dsh-agent";
import type { Agent, PreStepDecision } from "@deepseek-ai/dsh-agent";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import type { Session, SessionEvent } from "@deepseek-ai/dsh-session";
import type { ZepClient } from "@getzep/zep-cloud";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_CONTEXT_TEMPLATE,
  ZepMemoryRuntime,
  installZepMemory,
} from "../src/index.js";

function clientMock() {
  return {
    user: {
      add: vi.fn().mockResolvedValue({}),
    },
    thread: {
      create: vi.fn().mockResolvedValue({}),
      getUserContext: vi.fn().mockResolvedValue({ context: "Ada prefers dark mode." }),
      addMessages: vi.fn().mockResolvedValue({}),
    },
  };
}

function session(events: SessionEvent[] = []): Session {
  return {
    id: "session-1",
    events,
  } as unknown as Session;
}

function turnEvents(): SessionEvent[] {
  return [
    {
      type: "turn/start",
      seq: 0,
      time: 1,
      data: { turn: 1 },
    },
    {
      type: "user/message",
      seq: 1,
      time: 2,
      data: {
        id: "u1",
        role: "user",
        content: [{ type: "text", text: "Remember that I prefer dark mode." }],
        source: { kind: "user" },
      },
      surfaceOp: "append",
    },
    {
      type: "user/message",
      seq: 2,
      time: 3,
      data: {
        id: "memory",
        role: "user",
        content: [{ type: "text", text: "Injected Zep context" }],
        source: { kind: "plugin", plugin: "zep-memory" },
      },
      surfaceOp: "append",
    },
    {
      type: "assistant/message",
      seq: 3,
      time: 4,
      data: {
        turn: 1,
        step: 1,
        message: {
          id: "a1",
          role: "assistant",
          content: [
            { type: "text", text: "Let me check." },
            { type: "tool-call", id: "call-1", name: "lookup", arguments: "{}" },
          ],
          source: { kind: "model", provider: "deepseek", model: "chat" },
        },
      },
      surfaceOp: "append",
    },
    {
      type: "assistant/message",
      seq: 4,
      time: 5,
      data: {
        turn: 1,
        step: 2,
        message: {
          id: "a2",
          role: "assistant",
          content: [{ type: "text", text: "I'll remember that." }],
          source: { kind: "model", provider: "deepseek", model: "chat" },
        },
      },
      surfaceOp: "append",
    },
    {
      type: "turn/end",
      seq: 5,
      time: 6,
      data: { turn: 1, reason: { kind: "completed" } },
    },
  ] as unknown as SessionEvent[];
}

describe("ZepMemoryRuntime", () => {
  let mock: ReturnType<typeof clientMock>;
  let runtime: ZepMemoryRuntime;

  beforeEach(() => {
    mock = clientMock();
    runtime = new ZepMemoryRuntime({
      client: mock as unknown as ZepClient,
      userId: "user-1",
      firstName: "Ada",
      contextTemplate: DEFAULT_CONTEXT_TEMPLATE,
    });
  });

  it("provisions resources once and returns formatted context", async () => {
    await expect(runtime.recall("session-1")).resolves.toBe(
      "Relevant long-term memory from Zep:\n\nAda prefers dark mode.",
    );
    await runtime.recall("session-1");

    expect(mock.user.add).toHaveBeenCalledOnce();
    expect(mock.thread.create).toHaveBeenCalledOnce();
    expect(mock.thread.create).toHaveBeenCalledWith({
      threadId: "session-1",
      userId: "user-1",
    });
    expect(mock.thread.getUserContext).toHaveBeenCalledTimes(2);
  });

  it("persists direct user text and only the final assistant response", async () => {
    await runtime.persistTurn(session(turnEvents()), 1);

    expect(mock.thread.addMessages).toHaveBeenCalledOnce();
    expect(mock.thread.addMessages).toHaveBeenCalledWith("session-1", {
      messages: [
        {
          role: "user",
          content: "Remember that I prefer dark mode.",
        },
        {
          role: "assistant",
          content: "I'll remember that.",
          name: "Assistant",
        },
      ],
    });
  });

  it("fails open when context retrieval fails", async () => {
    mock.thread.getUserContext.mockRejectedValueOnce(new Error("offline"));
    await expect(runtime.recall("session-1")).resolves.toBe("");
  });

  it("rejects a context template without one marker", () => {
    expect(
      () =>
        new ZepMemoryRuntime({
          client: mock as unknown as ZepClient,
          userId: "user-1",
          contextTemplate: "memory",
        }),
    ).toThrow(/exactly one/);
  });
});

describe("installZepMemory", () => {
  it("recalls only for genuine user input and logs the injected context", async () => {
    const ctx = new Context();
    const mock = clientMock();
    const runtime = new ZepMemoryRuntime({
      client: mock as unknown as ZepClient,
      userId: "user-1",
    });
    installZepMemory(ctx, runtime, { persist: false });
    const currentSession = session();
    const agent = { id: "session-1", session: currentSession } as unknown as Agent;
    const direct = createUserMessage({
      content: [{ type: "text", text: "What do you remember?" }],
      source: { kind: "user" },
    });

    const decision = await agentEvents(ctx, agent).waterfall(
      "agent/pre-step",
      {
        messages: [direct],
        turn: 1,
        step: 1,
        signal: new AbortController().signal,
      },
      () =>
        Promise.resolve<PreStepDecision>({
          kind: "enter",
          messages: [direct],
        }),
    );

    expect(decision.kind).toBe("enter");
    if (decision.kind === "enter") {
      expect(decision.messages).toHaveLength(2);
      expect(decision.messages[1]?.source).toMatchObject({
        kind: "plugin",
        plugin: "zep-memory",
        form: "snapshot",
      });
    }
    await ctx.fiber.dispose();
  });

  it("persists each successful turn-end notification once", async () => {
    const ctx = new Context();
    const mock = clientMock();
    const runtime = new ZepMemoryRuntime({
      client: mock as unknown as ZepClient,
      userId: "user-1",
    });
    const handle = installZepMemory(ctx, runtime, { recall: false });
    const currentSession = session(turnEvents());
    const end = turnEvents().at(-1);
    if (end === undefined) throw new Error("missing turn/end fixture");

    ctx.emit("session/event", currentSession, end);
    ctx.emit("session/event", currentSession, end);
    await handle.flush();

    expect(mock.thread.addMessages).toHaveBeenCalledOnce();
    await ctx.fiber.dispose();
  });
});
