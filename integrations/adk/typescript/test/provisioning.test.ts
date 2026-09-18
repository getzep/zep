/**
 * Tests for explicit, out-of-band Zep provisioning helpers
 * (`src/provisioning.ts`).
 *
 * `createUser` / `createThread` provision Zep resources out-of-band (before
 * the first agent turn). Zep v4 assigns every UUID on the server, so each
 * helper returns the UUIDs of the new resource and throws on any failure.
 */

import { describe, expect, it, vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import { createThread, createUser } from "../src/provisioning.js";
import {
  MOCK_GRAPH_UUID,
  MOCK_THREAD_UUID,
  MOCK_USER_UUID,
  mockZepClient,
} from "./helpers.js";

class FakeApiError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

describe("createUser", () => {
  it("returns the UUID of the user and of its graph", async () => {
    const { client, mocks } = mockZepClient();

    const result = await createUser(client as unknown as ZepClient, {
      userId: "user-1",
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });

    expect(result).toEqual({
      userUuid: MOCK_USER_UUID,
      graphUuid: MOCK_GRAPH_UUID,
    });
    expect(mocks.userCreate).toHaveBeenCalledWith({
      userId: "user-1",
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
  });

  it("creates a user without any developer-assigned name", async () => {
    const { client, mocks } = mockZepClient();

    const result = await createUser(client as unknown as ZepClient);

    expect(result.userUuid).toBe(MOCK_USER_UUID);
    expect(mocks.userCreate).toHaveBeenCalledWith({
      userId: undefined,
      firstName: undefined,
      lastName: undefined,
      email: undefined,
    });
  });

  it("throws when the response carries no UUID", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userCreate.mockResolvedValueOnce({ userId: "user-1" });

    await expect(
      createUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("did not return a UUID");
  });

  it("throws on a genuine failure (5xx)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userCreate.mockRejectedValueOnce(
      new FakeApiError("internal error", 500),
    );

    await expect(
      createUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("internal error");
  });

  it("throws on a generic exception", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userCreate.mockRejectedValueOnce(new Error("network timeout"));

    await expect(
      createUser(client as unknown as ZepClient, { userId: "user-1" }),
    ).rejects.toThrow("network timeout");
  });

  it("runs onCreated exactly once with the UUID of the new user", async () => {
    const { client } = mockZepClient();
    const onCreated = vi.fn().mockResolvedValue(undefined);

    await createUser(client as unknown as ZepClient, { onCreated });

    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onCreated).toHaveBeenCalledWith(client, MOCK_USER_UUID);
  });

  it("does not run onCreated when the create call fails", async () => {
    const { client, mocks } = mockZepClient();
    mocks.userCreate.mockRejectedValueOnce(new Error("internal error"));
    const onCreated = vi.fn().mockResolvedValue(undefined);

    await expect(
      createUser(client as unknown as ZepClient, { onCreated }),
    ).rejects.toThrow("internal error");
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("propagates an error from onCreated", async () => {
    const { client } = mockZepClient();
    const onCreated = vi.fn().mockRejectedValue(new Error("setup failed"));

    await expect(
      createUser(client as unknown as ZepClient, { onCreated }),
    ).rejects.toThrow("setup failed");
  });

  it("awaits onCreated before it returns", async () => {
    const { client } = mockZepClient();
    let hookCompleted = false;
    const onCreated = vi.fn().mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      hookCompleted = true;
    });

    await createUser(client as unknown as ZepClient, { onCreated });

    expect(hookCompleted).toBe(true);
  });
});

describe("createThread", () => {
  it("returns the UUID of the thread and of its graph", async () => {
    const { client, mocks } = mockZepClient();

    const result = await createThread(client as unknown as ZepClient, {
      userUuid: MOCK_USER_UUID,
      threadId: "thread-1",
    });

    expect(result).toEqual({
      threadUuid: MOCK_THREAD_UUID,
      graphUuid: MOCK_GRAPH_UUID,
    });
    expect(mocks.threadCreate).toHaveBeenCalledWith({
      userUuid: MOCK_USER_UUID,
      threadId: "thread-1",
    });
  });

  it("creates a thread without any developer-assigned name", async () => {
    const { client, mocks } = mockZepClient();

    const result = await createThread(client as unknown as ZepClient, {
      userUuid: MOCK_USER_UUID,
    });

    expect(result.threadUuid).toBe(MOCK_THREAD_UUID);
    expect(mocks.threadCreate).toHaveBeenCalledWith({
      userUuid: MOCK_USER_UUID,
      threadId: undefined,
    });
  });

  it("throws when the response carries no UUID", async () => {
    const { client, mocks } = mockZepClient();
    mocks.threadCreate.mockResolvedValueOnce({ userUuid: MOCK_USER_UUID });

    await expect(
      createThread(client as unknown as ZepClient, {
        userUuid: MOCK_USER_UUID,
      }),
    ).rejects.toThrow("did not return a UUID");
  });

  it("throws on a genuine failure (5xx)", async () => {
    const { client, mocks } = mockZepClient();
    mocks.threadCreate.mockRejectedValueOnce(
      new FakeApiError("internal error", 500),
    );

    await expect(
      createThread(client as unknown as ZepClient, {
        userUuid: MOCK_USER_UUID,
      }),
    ).rejects.toThrow("internal error");
  });
});
