import { describe, expect, it } from "vitest";
import {
  MESSAGE_CONTENT_MAX,
  MESSAGE_CONTENT_TRUNCATE_TO,
  truncateMessageContent,
} from "../src/limits.js";
import { capturingLogger } from "./helpers.js";

describe("truncateMessageContent", () => {
  it("returns short content unchanged without warning", () => {
    const logger = capturingLogger();
    const out = truncateMessageContent("hello", logger, "user");
    expect(out).toBe("hello");
    expect(logger.warns).toHaveLength(0);
  });

  it("returns content at the exact limit unchanged", () => {
    const logger = capturingLogger();
    const content = "a".repeat(MESSAGE_CONTENT_MAX);
    const out = truncateMessageContent(content, logger);
    expect(out.length).toBe(MESSAGE_CONTENT_MAX);
    expect(logger.warns).toHaveLength(0);
  });

  it("truncates over-long content to the truncation target", () => {
    const logger = capturingLogger();
    const content = "b".repeat(MESSAGE_CONTENT_MAX + 1);
    const out = truncateMessageContent(content, logger, "assistant");
    expect(out.length).toBe(MESSAGE_CONTENT_TRUNCATE_TO);
    expect(logger.warns).toHaveLength(1);
  });

  it("logs lengths but never the content (no PII leakage)", () => {
    const logger = capturingLogger();
    const secret = "S".repeat(MESSAGE_CONTENT_MAX + 2000);
    truncateMessageContent(secret, logger, "user");
    const warning = logger.warns[0];
    // Lengths present.
    expect(warning).toContain(String(MESSAGE_CONTENT_MAX + 2000));
    expect(warning).toContain(String(MESSAGE_CONTENT_TRUNCATE_TO));
    // Content absent (no run of the message body).
    expect(warning).not.toContain("SSSSS");
    // The label is included for debugging.
    expect(warning).toContain("user");
  });
});
