import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { parseCliArgs } from "./cli";

describe("chunking CLI", () => {
  it("requires a document path and --user-id", () => {
    assert.throws(
      () => parseCliArgs(["node", "index.js"]),
      /required|user-id|document/i,
    );
  });

  it("parses document, user-id, chunk-size, dry-run, and wait", () => {
    const args = parseCliArgs([
      "node",
      "index.js",
      "sample_document.txt",
      "--user-id",
      "user123",
      "--chunk-size",
      "4000",
      "--chunk-overlap",
      "200",
      "--dry-run",
      "--wait",
    ]);
    assert.equal(args.document, "sample_document.txt");
    assert.equal(args.userId, "user123");
    assert.equal(args.chunkSize, 4000);
    assert.equal(args.chunkOverlap, 200);
    assert.equal(args.dryRun, true);
    assert.equal(args.wait, true);
  });

  it("uses documented defaults for chunk size and overlap", () => {
    const args = parseCliArgs([
      "node",
      "index.js",
      "doc.txt",
      "--user-id",
      "u1",
    ]);
    assert.equal(args.chunkSize, 6000);
    assert.equal(args.chunkOverlap, 200);
    assert.equal(args.dryRun, false);
    assert.equal(args.wait, false);
  });
});
