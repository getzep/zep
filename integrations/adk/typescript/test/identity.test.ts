import { describe, expect, it } from "vitest";
import { extractText, resolveIdentity } from "../src/identity.js";
import { ZepIdentityError } from "../src/errors.js";
import { fakeContext } from "./helpers.js";

const USER_UUID = "123e4567-e89b-42d3-a456-426614174000";
const THREAD_UUID = "123e4567-e89b-42d3-a456-426614174001";

describe("resolveIdentity", () => {
  it("prefers explicit UUIDs over session state and ADK UUID fallbacks", () => {
    const ctx = fakeContext({
      userId: USER_UUID,
      sessionId: THREAD_UUID,
      state: { zep_user_uuid: USER_UUID, zep_thread_uuid: THREAD_UUID },
    });
    expect(
      resolveIdentity(ctx, { userUuid: "option-user", threadUuid: "option-thread" }),
    ).toMatchObject({ userUuid: "option-user", threadUuid: "option-thread" });
  });

  it("uses UUIDs from ADK state", () => {
    const identity = resolveIdentity(
      fakeContext({
        userId: "adk-user",
        sessionId: "adk-session",
        state: {
          zep_user_uuid: USER_UUID,
          zep_thread_uuid: THREAD_UUID,
          zep_graph_uuid: "123e4567-e89b-42d3-a456-426614174002",
        },
      }),
    );
    expect(identity).toMatchObject({
      userUuid: USER_UUID,
      threadUuid: THREAD_UUID,
    });
  });

  it("uses ADK identifiers only when they are Zep UUIDs", () => {
    expect(
      resolveIdentity(fakeContext({ userId: USER_UUID, sessionId: THREAD_UUID })),
    ).toMatchObject({ userUuid: USER_UUID, threadUuid: THREAD_UUID });
    const invalidContext = {
      ...fakeContext({}),
      userId: "adk-user",
      sessionId: "adk-session",
    };
    expect(() => resolveIdentity(invalidContext)).toThrow(ZepIdentityError);
  });

  it("builds a display name from first and last name", () => {
    expect(
      resolveIdentity(fakeContext({}), {
        userUuid: USER_UUID,
        threadUuid: THREAD_UUID,
        firstName: "Jane",
        lastName: "Doe",
      }).displayName,
    ).toBe("Jane Doe");
  });

  it("throws when no user UUID or thread UUID is available", () => {
    expect(() =>
      resolveIdentity(fakeContext({ userId: "", sessionId: "" })),
    ).toThrow(ZepIdentityError);
    expect(() =>
      resolveIdentity(fakeContext({ userId: USER_UUID, sessionId: "" })),
    ).toThrow(/thread UUID/i);
  });
});

describe("extractText", () => {
  it("joins text parts and ignores non-text parts", () => {
    expect(
      extractText({
        role: "user",
        parts: [{ text: "hello" }, { functionCall: { name: "t" } }, { text: "world" }],
      }),
    ).toBe("hello world");
  });

  it("returns undefined for empty or missing content", () => {
    expect(extractText(undefined)).toBeUndefined();
    expect(extractText({ role: "user", parts: [] })).toBeUndefined();
    expect(extractText({ role: "user", parts: [{ text: "" }] })).toBeUndefined();
  });
});
