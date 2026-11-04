# Tau3 Banking and AppWorld

This package contains two harnesses. Tau3 Banking tests skills on banking
tasks. AppWorld tests skills on app-based scenarios. Both harnesses use the
same Zep Agent Skills capture, compile, and search code.

## Setup

Use Python 3.12 and `uv`. Install the tau2 task data at the pinned revision:

```bash
git clone https://github.com/sierra-research/tau2-bench.git
git -C tau2-bench checkout b7ea9074c1cba482b30687fecdb5c8425fd6f619
export TAU2_DATA_DIR="$HOME/repos/tau2-bench/data"
uv sync --all-groups
```

Set `OPENAI_API_KEY` for model calls. Set `ZEP_API_KEY` for trace capture,
skill compilation, and search. The SDK uses its default Zep Cloud endpoint.
Set `ZEP_BASE_URL` only when you use another endpoint. Set `ANTHROPIC_API_KEY`
to run a `claude-*` model. That route calls Anthropic directly.
For an OpenAI-compatible agent endpoint, set `--agent-base-url` and
`--agent-api-key-env`. For DeepSeek, use `DEEPSEEK_BASE_URL` as the endpoint
and `DEEPSEEK_BEARER_TOKEN` as the key variable.

## Tau3 Banking

The user simulator defaults to `gpt-6-luna` with low reasoning through the
Responses API. The local simulator adapter preserves reasoning and tool output
items in the tau2 state. Responses calls do not support tau2's model seed.
Other user-model overrides keep the upstream tau2 simulator.

Run `python run.py --help` to list the stages. A run uses a teacher stage to
capture traces, a compile stage to create a frozen skill snapshot, and held-out
stages to compare the `none` and `skills` arms. Use the same frozen manifest
and snapshot for each arm.

The `publication-setup` stage creates a destination Agent. Configure its skill
publication policy in the Zep dashboard. The compile and student stages use
the public SDK to verify publication lineage and admission.

Tracing is optional. Braintrust tracing stays off unless its variables are
set. See `braintrust_tracing.py` for the variable names.

## AppWorld

Apply `appworld-star-import.patch` to the pinned AppWorld container source
before you build it. Use `appworld_run.py` for teacher and evaluation stages.
Pass your AppWorld source checkout with `--appworld-root`. Use
`appworld_replay.py` to replay saved episodes. The replay test uses a small
invented fixture.

The package pins tau2 at commit
`b7ea9074c1cba482b30687fecdb5c8425fd6f619`. Live runs need the task data and
the API credentials above. Tests do not call Zep Cloud or a model provider.

## Tau3 Banking result examples

The report used 97 tasks and four trials per model and effort. It used 100,000
paired bootstrap draws and Holm-adjusted tests across eight model-effort
comparisons.

| Model route | Effort | No skills | Skills | Gain (pp) | 95% CI (pp) | Adjusted p |
|---|---:|---:|---:|---:|---:|---:|
| gpt-6-luna | low | 51/388 | 84/388 | +8.51 | +3.35 to +13.92 | 0.02205 |
| claude-haiku-5-5 | medium | 82/388 | 142/388 | +15.46 | +9.54 to +21.65 | 0.00016 |
| deepseek-v4.1-flash-1017 | low | 163/388 | 197/388 | +8.76 | +3.09 to +14.69 | 0.03570 |

The gains are percentage-point changes in episode pass rate. These are
examples from the full report, not a result for every model and effort.

## Environment variables

| Variable | Use |
|---|---|
| `TAU2_DATA_DIR` | Path to the pinned tau2 task data |
| `OPENAI_API_KEY` | OpenAI-compatible model calls and task audits |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible endpoint override |
| `OPENROUTER_API_KEY` | OpenRouter agent model calls |
| `DEEPSEEK_BASE_URL` | Optional OpenAI-compatible DeepSeek endpoint |
| `DEEPSEEK_BEARER_TOKEN` | Bearer token for that endpoint |
| `ZEP_API_KEY` | Zep Cloud access |
| `ZEP_BASE_URL` | Optional Zep API base URL |
| `ANTHROPIC_API_KEY` | Direct Anthropic calls for `claude-*` models |
| `TAU_BENCH_SKILLS_CONCURRENCY` | Default harness concurrency |
| `LANGFUSE_PUBLIC_KEY` | Optional Langfuse tracing |
| `LANGFUSE_SECRET_KEY` | Optional Langfuse tracing |
| `LANGFUSE_BASE_URL` | Optional Langfuse endpoint |
| `LANGFUSE_HOST` | Legacy Langfuse endpoint override |
| `BRAINTRUST_API_KEY` | Optional Braintrust trace upload |
| `BRAINTRUST_API_URL` | Optional Braintrust API endpoint |
| `SKILLS_OVERRIDE_FILE` | Optional local skills override file |

Use `uv run --frozen pytest -q` to run the local tests. These tests skip live
tests and do not call Zep Cloud or a paid model API.
