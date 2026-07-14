# Zep + DaoXE (OpenAI-compatible LLM gateway)

Minimal example: use [Zep](https://www.getzep.com) agent memory with an
[OpenAI Python client](https://github.com/openai/openai-python) pointed at an
OpenAI-compatible chat Completions endpoint.

[DaoXE](https://daoxe.com) is used here as the gateway (`base_url=https://daoxe.com/v1`).
Any OpenAI-compatible provider works the same way — only the base URL, API key,
and model ID change.

## What this shows

1. Create a Zep user + thread and store turns with `thread.add_messages`.
2. Pull a context block with `thread.get_user_context`.
3. Call chat completions via the OpenAI SDK with a custom `base_url`.
4. Persist the assistant reply back into Zep.

## Requirements

- Python 3.10+
- Zep Cloud API key ([app.getzep.com](https://app.getzep.com))
- DaoXE API key ([daoxe.com](https://daoxe.com)) — **not available in mainland China**
- A model ID from your DaoXE account (`GET https://daoxe.com/v1/models`)

DaoXE is multi-protocol (OpenAI-compatible `/v1/chat/completions` and Anthropic-style
`/v1/messages`). This example uses only the OpenAI-compatible path so it drops into
existing OpenAI SDK code.

## Setup

```bash
cd examples/python/daoxe-openai-compatible
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with ZEP_API_KEY, DAOXE_API_KEY, and DAOXE_MODEL
```

## Run

```bash
python chat_with_memory.py
```

Expected flow: two turns against the same Zep thread. The second user message
asks about something said in the first turn so you can see Zep context influence
the reply.

## Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `ZEP_API_KEY` | yes | Zep Cloud API key |
| `DAOXE_API_KEY` | yes | DaoXE API key (Bearer token for `/v1`) |
| `DAOXE_MODEL` | yes | Exact model ID from your DaoXE account |
| `DAOXE_BASE_URL` | no | Defaults to `https://daoxe.com/v1` |

Prefer live model IDs from the account / `GET /v1/models` rather than hard-coding
a catalog.

## Notes

- Same pattern as other examples in this repo that construct `OpenAI(...)` /
  `AsyncOpenAI(...)` — the only difference is `base_url`.
- Do not use this path from mainland China; DaoXE is not available there.
- For Claude-protocol clients, DaoXE also exposes `POST /v1/messages` with the
  same key and account-scoped model IDs; that path is out of scope for this
  OpenAI SDK sample.
