# Agent Skills with LangGraph, Google ADK, and Claude Agent SDK

This example shows how to use Zep Agent Skills with LangGraph. The banking data
is invented. Read the [banking task](../agent-skills-banking/README.md) for its
task description.

The example uses a shared banking tool layer. Each framework adapter uses the
same tools and records the same Trace events.

## Learning loop

The teacher runs first. The example records a live Trace and checks the local
banking state. It closes the Trace with the result of that check.

Zep compiles a Skill from the closed Trace. The example searches the Skill
catalog and adds the Skill metadata to the student system prompt. The student
uses `read_skill` to get the full Markdown. The example records another live
Trace and checks the local banking state. It closes that Trace with the check
result, then records Skill use and its outcome.

## Setup

Use Python 3.12 or 3.13. Install the project and its development tools:

```bash
uv sync --group dev
```

Set `ANTHROPIC_API_KEY` and `ZEP_API_KEY`. You can set `ZEP_BASE_URL` to use a
custom Zep environment. The URL must use HTTPS.

## Framework adapters

### LangGraph

The LangGraph adapter uses a LangChain callback handler for Trace events.

```bash
uv run python run.py run --framework langgraph --output runs/langgraph-run
```

### Google ADK

The Google ADK adapter uses a `BasePlugin` for model and tool callbacks.

```bash
uv run python run.py run --framework adk --output runs/adk-run
```

### Claude Agent SDK

The Claude Agent SDK adapter uses `PreToolUse`, `PostToolUse`, and
`PostToolUseFailure` hooks.

```bash
uv run python run.py run --framework claude-agent-sdk --output runs/claude-agent-sdk-run
```

Each command runs a teacher and a Skill-assisted student. It uses
`claude-opus-5-5` for the teacher and `claude-haiku-5-5` for the student. It
writes the run artifacts under the output directory. The `runs/` directory is
ignored by Git.

## Checks

Run the tests, lint, format, type, and CLI checks:

```bash
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pyright
uv run --frozen python run.py run --help
```
