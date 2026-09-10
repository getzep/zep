import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { describe, it } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));

function runHelp(env: NodeJS.ProcessEnv): Promise<{
  code: number | null;
  stdout: string;
  stderr: string;
}> {
  return new Promise((resolve) => {
    const child = spawn("npx", ["tsx", "agent.ts", "--help"], {
      cwd: root,
      env: {
        ...env,
        // Ensure help does not depend on live keys
        TAVILY_API_KEY: "",
        OPENAI_API_KEY: env.OPENAI_API_KEY ?? "",
        ZEP_API_KEY: env.ZEP_API_KEY ?? "",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

describe("langgraph CLI help", () => {
  it("prints help and exits 0 without TAVILY_API_KEY", async () => {
    const result = await runHelp(process.env);
    assert.equal(
      result.code,
      0,
      `expected exit 0, got ${result.code}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    );
    assert.match(result.stdout, /LangGraph CLI Agent with Zep memory/i);
    assert.doesNotMatch(result.stderr, /Cannot find package '@langchain\/community'/);
  });
});
