# ALFWorld

This package tests skill transfer on text-based household tasks. The harness
has two arms: `none` and `skills`. The `teacher` stage captures training
traces. The `eval` stage runs the selected arm on `valid_seen` or
`valid_unseen`.

## Setup

Use Python 3.12, `uv`, and the ALFWorld 0.4.2 data files. Install the package:

```bash
uv sync --all-groups
alfworld-download
```

Set `ALFWORLD_DATA` when the data is not in `$HOME/alfworld/data`. Set
`ZEP_API_KEY` for trace capture and skill search. The SDK uses its default
endpoint unless `ZEP_BASE_URL` is set.

The model endpoint must implement the OpenAI Chat Completions API. Set
`ALFWORLD_MODEL_BASE_URL` and `ALFWORLD_BEARER_TOKEN`. The optional
`modal_qwen3_8b.py` file is an example for a Modal workspace that you control.
Create the `alfworld-model` Modal secret with the bearer token before you
deploy the example. The vLLM server uses that token for API requests.

## Results on unseen rooms

The test set has 134 `valid_unseen` games. Each value below is the success
rate reported in the benchmark comparison.

| Setup | No skills | With skills | Gain |
|---|---:|---:|---:|
| Fixed demonstration | 42.8% | 75.4% | +32.6 pp |
| Task-type demonstration | 74.6% | 82.8% | +8.2 pp |

The skills were compiled from 204 train runs into 22 skills. The evaluation
used three seeds. It reported no failed episodes and no format errors.

## Environment variables

| Variable | Use |
|---|---|
| `ALFWORLD_DATA` | ALFWorld data directory |
| `ALFWORLD_MODEL_BASE_URL` | OpenAI-compatible model endpoint |
| `ALFWORLD_BEARER_TOKEN` | Bearer token for the model endpoint |
| `ALFWORLD_MODEL` | Optional model name; defaults to `qwen3-8b` |
| `ALFWORLD_DEMO` | Set to `fixed` or `typed`; defaults to `typed` |
| `ZEP_API_KEY` | Zep Cloud access |
| `ZEP_BASE_URL` | Optional Zep API base URL |

Use `uv run --frozen pytest -q` to run the local tests. They do not call Zep
Cloud or a model API.
