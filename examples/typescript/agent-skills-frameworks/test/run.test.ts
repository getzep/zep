import { describe, expect, it, vi } from "vitest";
import { FRAMEWORKS, main } from "../src/run.js";

describe("framework selection", () => {
  it("loads an adapter for every supported framework", async () => {
    for (const framework of ["mastra", "codex", "eve"] as const) {
      expect(typeof (await FRAMEWORKS[framework]()).runEpisode).toBe("function");
    }
  });

  it("prints the framework choices in CLI help", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    await main(["--help"]);
    expect(log.mock.calls[0][0]).toContain("mastra|codex|eve");
    log.mockRestore();
  });
});
