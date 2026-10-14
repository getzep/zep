import { describe, expect, it, vi } from "vitest";
import type { ZepClient } from "@getzep/zep-cloud";
import { createThread, createUser } from "../src/provisioning.js";
import { mockZepClient } from "./helpers.js";

describe("createUser", () => {
  it("returns server-generated UUIDs and invokes onCreated with the user UUID", async () => {
    const { client, mocks } = mockZepClient();
    const onCreated = vi.fn();
    const result = await createUser(client as unknown as ZepClient, {
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
      onCreated,
    });

    expect(mocks.createUser).toHaveBeenCalledWith({
      firstName: "Jane",
      lastName: "Smith",
      email: "jane@example.com",
    });
    expect(result).toEqual({
      userUuid: "user-uuid",
      graphUuid: "graph-uuid",
    });
    expect(onCreated).toHaveBeenCalledWith(client, "user-uuid");
  });

  it("propagates provisioning and hook errors", async () => {
    const { client, mocks } = mockZepClient();
    mocks.createUser.mockRejectedValueOnce(new Error("create failed"));
    await expect(createUser(client as unknown as ZepClient)).rejects.toThrow(
      "create failed",
    );

    const onCreated = vi.fn().mockRejectedValue(new Error("setup failed"));
    await expect(
      createUser(client as unknown as ZepClient, { onCreated }),
    ).rejects.toThrow("setup failed");
  });
});

describe("createThread", () => {
  it("creates a thread with the user UUID and returns server-generated IDs", async () => {
    const { client, mocks } = mockZepClient();
    const result = await createThread(client as unknown as ZepClient, {
      userUuid: "user-uuid",
    });

    expect(mocks.createThread).toHaveBeenCalledWith({ userUuid: "user-uuid" });
    expect(result).toEqual({
      threadUuid: "thread-uuid",
      graphUuid: "graph-uuid",
    });
  });

  it("does not return an invalid SDK identity", async () => {
    const { client, mocks } = mockZepClient();
    mocks.createThread.mockResolvedValueOnce({ uuid: "", graphUuid: "" });
    await expect(
      createThread(client as unknown as ZepClient, { userUuid: "user-uuid" }),
    ).rejects.toThrow();
  });
});
