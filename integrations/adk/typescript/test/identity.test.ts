import { describe, expect, it } from "vitest";
import { extractText, resolveIdentity } from "../src/identity.js";
import { ZepIdentityError } from "../src/errors.js";
import { fakeContext } from "./helpers.js";

describe("resolveIdentity", () => {
  it("falls back to the ADK session userId/sessionId", () => {
    const id = resolveIdentity(
      fakeContext({ userId: "adk-u", sessionId: "adk-s" }),
    );
    expect(id.userId).toBe("adk-u");
    expect(id.threadId).toBe("adk-s");
  });

  it("prefers explicit options, then state, then ADK session", () => {
    const ctx = fakeContext({
      userId: "adk-u",
      sessionId: "adk-s",
      state: { zep_user_id: "state-u", zep_thread_id: "state-s" },
    });
    expect(resolveIdentity(ctx).userId).toBe("state-u");
    expect(resolveIdentity(ctx, { userId: "opt-u" }).userId).toBe("opt-u");
  });

  it("builds a display name from first and last name", () => {
    const id = resolveIdentity(fakeContext({ userId: "u", sessionId: "s" }), {
      firstName: "Jane",
      lastName: "Doe",
    });
    expect(id.displayName).toBe("Jane Doe");
  });

  it("leaves displayName undefined when no name is available", () => {
    const id = resolveIdentity(fakeContext({ userId: "u", sessionId: "s" }));
    expect(id.displayName).toBeUndefined();
  });

  it("throws ZepIdentityError when no userId can be resolved", () => {
    const ctx = fakeContext({ userId: "", sessionId: "s" });
    expect(() => resolveIdentity(ctx)).toThrow(ZepIdentityError);
  });

  it("throws ZepIdentityError when no threadId can be resolved", () => {
    const ctx = fakeContext({ userId: "u", sessionId: "" });
    expect(() => resolveIdentity(ctx)).toThrow(/thread ID/);
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
