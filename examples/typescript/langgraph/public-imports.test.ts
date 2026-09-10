import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describe, it } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));

describe("langgraph public SDK imports", () => {
  it("does not import non-public @getzep/zep-cloud subpaths", async () => {
    const files = ["agent.ts", "zep-memory.ts", "tools.ts"];
    const forbidden = /from\s+["']@getzep\/zep-cloud\/(?!serialization(?:["']|$))[^"']+["']/;
    for (const file of files) {
      const source = await readFile(path.join(root, file), "utf8");
      assert.equal(
        forbidden.test(source),
        false,
        `${file} must not import non-public @getzep/zep-cloud subpaths`,
      );
    }
  });

  it("uses Zep namespace types from the package root", async () => {
    const source = await readFile(path.join(root, "zep-memory.ts"), "utf8");
    assert.match(source, /import\s*\{[^}]*Zep[^}]*\}\s*from\s*["']@getzep\/zep-cloud["']/);
    assert.doesNotMatch(source, /from\s+["']@getzep\/zep-cloud\/dist/);
  });
});
