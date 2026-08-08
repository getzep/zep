# Zep + DaoXE (OpenAI-compatible Chat Completions)

Minimal Python example: **AsyncZep** long-term memory with an **AsyncOpenAI** client pointed at [DaoXE](https://daoxe.com).

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.environ["DAOXE_API_KEY"],
    base_url="https://daoxe.com/v1",
)
```

## Why this example

Many Zep samples already use the OpenAI Python SDK for the LLM turn. DaoXE is a **multi-model, multi-protocol** API gateway. This sample shows the same migration path used elsewhere in the ecosystem:

| Piece | Value |
| --- | --- |
| Chat Completions base | `https://daoxe.com/v1` |
| Auth | `DAOXE_API_KEY` |
| Model | `DAOXE_MODEL` (exact ID from **your** DaoXE account catalog) |
| Memory | Zep Cloud (`ZEP_API_KEY`) via `zep-cloud` |

DaoXE also supports other protocols (including **OpenAI Responses** and **Anthropic Messages / Claude protocol**) and multiple model families. This folder only covers OpenAI SDK + custom `base_url` so it stays consistent with existing OpenAI-style Zep examples — it is **not** OpenAI-only.

> **Availability:** DaoXE is **not offered in mainland China**. Use account-visible model IDs from your own DaoXE dashboard/catalog. If you cannot reach `daoxe.com`, follow regional endpoint guidance on the DaoXE site (do not hardcode alternate hosts in application code without checking current docs).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Variable | Source |
| --- | --- |
| `ZEP_API_KEY` | [app.getzep.com](https://app.getzep.com) |
| `DAOXE_API_KEY` | [daoxe.com](https://daoxe.com) dashboard |
| `DAOXE_MODEL` | Exact model ID from your DaoXE account (catalog / `GET /v1/models`) |
| `DAOXE_BASE_URL` | Optional; defaults to `https://daoxe.com/v1` |

Model IDs are **account-scoped** and change over time. Prefer env vars over hardcoding keys or model names.

## Run

```bash
python daoxe_zep_memory.py
```

What it does:

1. Creates a Zep user and seeds a short prior conversation (walking tours in Lisbon).
2. Opens a live thread and asks a follow-up that should use retrieved memory.
3. Streams the assistant reply from DaoXE via `AsyncOpenAI(... base_url=https://daoxe.com/v1)`.
4. Writes both user and assistant turns back into the Zep thread.

## Notes

- Prefer models in your catalog that stream Chat Completions reliably.
- For Anthropic Messages / Claude protocol clients, use DaoXE’s Messages endpoints instead of this OpenAI SDK sample.
- More DaoXE client snippets: https://github.com/seven7763/DaoXE-AI

## Learn more

- [Zep Cloud docs](https://help.getzep.com)
- [DaoXE](https://daoxe.com)
