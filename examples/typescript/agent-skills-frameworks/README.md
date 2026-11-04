# Agent Skills with Mastra, Codex, and Eve

This example shows how Mastra, the OpenAI Codex SDK, and Eve can use Zep Agent Skills with the same invented banking task. The banking records contain synthetic data.

The example runs a teacher agent, records a live Trace, checks the local banking state, and closes the Trace with external verification. Zep then compiles a Skill. A student agent searches for and uses that Skill. The example records the student's Trace, checks the final state, and saves the Skill-use outcome.

## Requirements

Use Node.js 24 or later. The tested runtime is Node.js 24.22.2. Install the package dependencies:

```bash
npm ci
```

Set `ZEP_API_KEY` and, when needed, `ZEP_BASE_URL`. Mastra and Eve also need `ANTHROPIC_API_KEY`. Codex needs `OPENAI_API_KEY` or a configured Codex login. The example uses direct provider calls. It does not use LiteLLM or an AI Gateway.

## Mastra

Mastra uses its `onInputAvailable` and `onOutput` tool callbacks to record tool events. Its `onStepFinish` callback records assistant text. Anthropic tool calls run in sequence.

```bash
npm run run -- run --framework mastra --output runs/mastra-demo
```

## OpenAI Codex SDK

Codex uses a local stdio MCP server for the nine banking tools. The adapter records `runStreamed` events. It runs in a temporary read-only directory, disables network and web search, and turns off the installed shell, code-mode, browser, computer-use, image, and app tools. The installed Codex model catalog describes `gpt-6-astra` as its frontier model and `gpt-6-luna` as fast and affordable. The teacher uses `gpt-6-astra` at `ultra`, the highest effort that the installed SDK supports. The student uses `gpt-6-luna` at low effort.

```bash
npm run run -- run --framework codex --output runs/codex-demo
```

## Eve

Eve runs a local headless server with `eve dev --no-ui`. The adapter creates one temporary project and one client session for each episode. Eve's `call.requested`, `call.settled`, and `content.completed` hooks record live Trace events. A JSON state file carries the banking database back to the runner.

```bash
npm run run -- run --framework eve --output runs/eve-demo
```

## Tests

```bash
npx vitest run
npx tsc --noEmit
npm run run -- --help
git diff --check
```
