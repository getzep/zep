import { describe, it, expect, vi } from "vitest";
import { toRoleType, resolveGraphTarget } from "../src/index.js";
import {
  truncateForZep,
  MESSAGE_MAX_CHARS,
  GRAPH_MAX_CHARS,
} from "../src/zep-utils.js";

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

describe("truncateForZep", () => {
  it("returns content unchanged when within the limit", () => {
    const warn = vi.fn();
    const content = "x".repeat(100);
    expect(truncateForZep(content, 100, "test", { warn })).toBe(content);
    expect(warn).not.toHaveBeenCalled();
  });

  it("truncates to the limit and warns when over", () => {
    const warn = vi.fn();
    const result = truncateForZep("y".repeat(150), 100, "test", { warn });
    expect(result.length).toBe(100);
    expect(warn).toHaveBeenCalledOnce();
  });

  it("logs only lengths in the warning — never the content (no PII)", () => {
    const warn = vi.fn();
    const secret = "SSN-123-45-6789".repeat(50);
    truncateForZep(secret, 100, "test", { warn });
    const message = warn.mock.calls[0]![0] as string;
    expect(message).toContain(String(secret.length));
    expect(message).toContain("100");
    expect(message).not.toContain("SSN");
  });

  it("exposes the documented Zep limits", () => {
    expect(MESSAGE_MAX_CHARS).toBeLessThanOrEqual(4096);
    expect(GRAPH_MAX_CHARS).toBe(9900);
  });
});
