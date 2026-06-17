import { describe, it, expect } from "vitest";
import { toRoleType, resolveGraphTarget } from "../src/index.js";

describe("toRoleType", () => {
  it("passes through valid Zep roles", () => {
    expect(toRoleType("user")).toBe("user");
    expect(toRoleType("assistant")).toBe("assistant");
    expect(toRoleType("system")).toBe("system");
    expect(toRoleType("tool")).toBe("tool");
    expect(toRoleType("function")).toBe("function");
  });

  it("maps common aliases", () => {
    expect(toRoleType("Human")).toBe("user");
    expect(toRoleType("AI")).toBe("assistant");
    expect(toRoleType("bot")).toBe("assistant");
    expect(toRoleType("developer")).toBe("system");
  });

  it("falls back to norole for unknown or empty input", () => {
    expect(toRoleType("wizard")).toBe("norole");
    expect(toRoleType(undefined)).toBe("norole");
    expect(toRoleType("")).toBe("norole");
  });
});

describe("resolveGraphTarget", () => {
  it("prefers userId over graphId", () => {
    expect(resolveGraphTarget({ userId: "u", graphId: "g" })).toEqual({ userId: "u" });
  });
  it("falls back to graphId", () => {
    expect(resolveGraphTarget({ graphId: "g" })).toEqual({ graphId: "g" });
  });
  it("returns null when nothing is bound", () => {
    expect(resolveGraphTarget({})).toBeNull();
  });
});
