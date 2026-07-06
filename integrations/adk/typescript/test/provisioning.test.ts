/**
 * Tests for explicit, out-of-band Zep provisioning helpers
 * (`src/provisioning.ts`).
 *
 * `ensureUser` / `ensureThread` idempotently provision Zep resources
 * out-of-band (before the first agent turn), returning whether the resource
 * was newly created and throwing on genuine failures.
 */

import { describe, expect, it, vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import { ensureThread, ensureUser } from "../src/provisioning.js";
import { mockZepClient } from "./helpers.js";

class FakeApiError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

describe("ensureUser", () => {
  it("ensureUser returns true when created", async () => {
    const { client, mocks } = mockZepClient();

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });

    expect(result).toBe(true);
    expect(mocks.userAdd).toHaveBeenCalledWith({
      userId: "user-1",
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
  });

  it("ensureUser returns true when created with only userId (fields default to undefined)", async () => {
    const { client, mocks } = mockZepClient();

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
    });

    expect(result).toBe(true);
    expect(mocks.userAdd).toHaveBeenCalledWith({
      userId: "user-1",
      firstName: undefined,
      lastName: undefined,
      email: undefined,
    });
  });

  it("ensureUser returns false on 409 conflict (already-exists shape)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(
      new FakeApiError("User already exists", 409),
    );

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
    });

    expect(result).toBe(false);
  });

  it("ensureUser returns false on 400 'already exists' message shape", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(
      new FakeApiError("user already exists", 400),
    );

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
    });

    expect(result).toBe(false);
  });

  it("ensureUser throws on a genuine failure (5xx)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(
      new FakeApiError("internal error", 500),
    );

    await expect(
      ensureUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("internal error");
  });

  it("ensureUser throws on a typed 5xx whose message mentions 'conflict'", async () => {
    // The message-substring fallback applies to untyped errors only: a known
    // non-conflict status code is a genuine failure regardless of wording.
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(
      new FakeApiError("transaction conflict, please retry", 500),
    );

    await expect(
      ensureUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("transaction conflict");
  });

  it("ensureUser returns false on an untyped 'already exists' error (legacy shape)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(new Error("user already exists"));

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
    });

    expect(result).toBe(false);
  });

  it("ensureUser throws on a generic non-conflict exception", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(new Error("network timeout"));

    await expect(
      ensureUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("network timeout");
  });

  it("ensureUser does not throw on a 404 not-found shape treated as genuine failure", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(new FakeApiError("not found", 404));

    await expect(
      ensureUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("not found");
  });

  it("onCreated runs exactly once when the user is newly created", async () => {
    const { client } = mockZepClient();
    const onCreated = vi.fn().mockResolvedValue(undefined);

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
      onCreated,
    });

    expect(result).toBe(true);
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onCreated).toHaveBeenCalledWith(client, "user-1");
  });

  it("onCreated is not called when the user already exists", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(
      new FakeApiError("already exists", 409),
    );
    const onCreated = vi.fn().mockResolvedValue(undefined);

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
      onCreated,
    });

    expect(result).toBe(false);
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("onCreated errors propagate out of ensureUser", async () => {
    const { client } = mockZepClient();
    const onCreated = vi.fn().mockRejectedValue(new Error("setup failed"));

    await expect(
      ensureUser(client as unknown as ZepClient, {
        userId: "user-1",
        onCreated,
      }),
    ).rejects.toThrow("setup failed");
  });

  it("onCreated is awaited before ensureUser resolves", async () => {
    const { client } = mockZepClient();
    let hookCompleted = false;
    const onCreated = vi.fn().mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      hookCompleted = true;
    });

    await ensureUser(client as unknown as ZepClient, {
      userId: "user-1",
      onCreated,
    });

    expect(hookCompleted).toBe(true);
  });

  it("a racing conflict on ensureUser returns false", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userAdd.mockRejectedValueOnce(new FakeApiError("conflict", 409));

    const result = await ensureUser(client as unknown as ZepClient, {
      userId: "racer",
    });

    expect(result).toBe(false);
  });
});

describe("ensureThread", () => {
  it("ensureThread returns true when created", async () => {
    const { client, mocks } = mockZepClient();

    const result = await ensureThread(client as unknown as ZepClient, {
      threadId: "thread-1",
      userId: "user-1",
    });

    expect(result).toBe(true);
    expect(mocks.create).toHaveBeenCalledWith({
      threadId: "thread-1",
      userId: "user-1",
    });
  });

  it("ensureThread returns false on 409 conflict (already-exists shape)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.create.mockRejectedValueOnce(
      new FakeApiError("Thread already exists", 409),
    );

    const result = await ensureThread(client as unknown as ZepClient, {
      threadId: "thread-1",
      userId: "user-1",
    });

    expect(result).toBe(false);
  });

  it("ensureThread returns false on 400 'already exists' message shape", async () => {
    const { client, mocks } = mockZepClient();
    mocks.create.mockRejectedValueOnce(
      new FakeApiError("thread already exists", 400),
    );

    const result = await ensureThread(client as unknown as ZepClient, {
      threadId: "thread-1",
      userId: "user-1",
    });

    expect(result).toBe(false);
  });

  it("ensureThread throws on a genuine failure (5xx)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.create.mockRejectedValueOnce(
      new FakeApiError("internal error", 500),
    );

    await expect(
      ensureThread(client as unknown as ZepClient, {
        threadId: "thread-1",
        userId: "user-1",
      }),
    ).rejects.toThrow("internal error");
  });

  it("a racing conflict on ensureThread returns false", async () => {
    const { client, mocks } = mockZepClient();
    mocks.create.mockRejectedValueOnce(new FakeApiError("conflict", 409));

    const result = await ensureThread(client as unknown as ZepClient, {
      threadId: "thread-1",
      userId: "user-1",
    });

    expect(result).toBe(false);
  });
});
