# EnterpriseOps-Gym

This package tests skills on enterprise tool-use tasks. The harness supports
the `none`, `skills`, and `skills_decided` arms. The `skills` arm provides a
skill index and a `read_skill` tool. The `skills_decided` arm places one skill
selected by the model in the prompt.

## Data and container patches

The 13 revised task definitions come from the public
`ServiceNow/EnterpriseOps-Gym` repository at commit
`724dd34c09fa2bb6bdb76f8b8d5975350d648732`. The README in
`data/revised/SOURCE.md` records this source.

The `splits/split-v1.json` file lists task IDs from the public
`ServiceNow-AI/EnterpriseOps-Gym` oracle dataset at revision
`c8e538eae8a6205294f0a86675fefdc1fac408f6`. It also records the harness
clusters and teach or score labels. It contains no task text or model outputs.
The task rows and model outputs from our runs are not included.

The Teams and ITSM container patches are in `containers/`. Build and start
the services with the scripts in `scripts/`. The task data requires the
upstream seed databases. Set `EOG_DATA_ROOT` to the directory that contains
those seed files.

The repository does not include a no-op baseline. Generate it locally with
`scripts/make_noop_baseline.py` after you start the required Gym services.
Pass the generated file with `--noop-baseline` when you run a stage. Do not
commit the generated file.

## Run

Install Python 3.12 and Docker. Then run:

```bash
uv sync --all-groups
uv run --frozen python -m eog.run --help
uv run --frozen python -m eog.preflight
```

Use `--stage teacher` to capture traces and compile skills. Use `--stage
executor` to compare the arms with a frozen skill snapshot. The run manifests
record the task split, container image digests, patch hashes, and run flags.
The optional `--langfuse` flag enables tracing. It is off by default. The
`claude_code` teacher harness requires the `claude` command in `PATH`.

## Zep and model settings

Set `ZEP_API_KEY` to use Zep Cloud. The SDK default endpoint is used unless
`ZEP_BASE_URL` is set. Do not put API keys in a run manifest.

The harness supports Anthropic, OpenAI-compatible, OpenRouter, Z.AI, and
DeepSeek routes. Set the provider key for the selected model. DeepSeek uses
`DEEPSEEK_BASE_URL` and `DEEPSEEK_BEARER_TOKEN`; the endpoint must use HTTPS.
The endpoint must implement the OpenAI Chat Completions API.

## Environment variables

| Variable | Use |
|---|---|
| `ZEP_API_KEY` | Zep Cloud access |
| `ZEP_BASE_URL` | Optional Zep API base URL |
| `OPENAI_API_KEY` | OpenAI and skill-selection model calls |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible endpoint for Decisions API calls |
| `OPENROUTER_API_KEY` | OpenRouter model calls |
| `EOG_OPENROUTER_PROVIDERS` | Optional comma-separated OpenRouter provider order |
| `ANTHROPIC_API_KEY` | Direct Anthropic model calls |
| `ANTHROPIC_BASE_URL` | Optional Anthropic API endpoint for the Claude Code harness |
| `Z_AI_KEY` | Z.AI model calls |
| `DEEPSEEK_BASE_URL` | OpenAI-compatible DeepSeek endpoint |
| `DEEPSEEK_BEARER_TOKEN` | Bearer token for that endpoint |
| `EOG_DATA_ROOT` | Directory with upstream task seed databases |
| `EOG_RUN_LIVE_GYM` | Set to `1` to enable live Gym tests |
| `EOG_RUN_LIVE` | Set to `1` to enable live model-route tests |
| `LANGFUSE_PUBLIC_KEY` | Optional Langfuse tracing |
| `LANGFUSE_SECRET_KEY` | Optional Langfuse tracing |
| `LANGFUSE_BASE_URL` | Optional Langfuse endpoint |
| `LANGFUSE_HOST` | Optional Langfuse endpoint for report scripts |

The local test suite does not make provider calls. Use `uv run --frozen pytest
-q` to run it.
