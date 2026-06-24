# Setup Guide

This guide walks you from a fresh machine to running the example voice agent with Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get an OpenAI API key (for the example)

The bundled voice examples use OpenAI for STT, LLM, and TTS. Create a key at
[platform.openai.com/api-keys](https://platform.openai.com/api-keys) and copy it
for `OPENAI_API_KEY`.

## 3. Get LiveKit server credentials (for the voice examples)

The examples are LiveKit agent workers, so running them end-to-end needs a
LiveKit server (LiveKit Cloud or self-hosted). Create a project at
[cloud.livekit.io](https://cloud.livekit.io) and copy its URL, API key, and API
secret for `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.

You do **not** need LiveKit credentials to run the live integration test (step 6),
which validates the Zep memory layer directly without a voice session.

## 4. Install

Using `pip`:

```bash
pip install zep-livekit
```

Or, to work from the repository with `uv`:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/livekit/python
make install        # uv sync --extra dev
```

Requirements: Python 3.11+, `livekit-agents[openai,silero]>=1.0.0`, `zep-cloud>=3.23.0`.

## 5. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"

# Required only to run the voice examples (not the live test):
export LIVEKIT_URL="wss://your-project.livekit.cloud"
export LIVEKIT_API_KEY="your-livekit-api-key"
export LIVEKIT_API_SECRET="your-livekit-api-secret"
```

## 6. Run the example

The voice examples run as LiveKit agent workers. With the LiveKit variables set:

```bash
uv run python examples/voice_assistant.py dev
```

Then connect a client (e.g. the [LiveKit Agents Playground](https://agents-playground.livekit.io))
to talk to the agent. As the conversation proceeds, `ZepUserAgent` persists turns
to Zep and injects recalled context on later turns.

Other runnable examples live in [`examples/`](examples):

- `voice_assistant.py` — `ZepUserAgent` with thread-based conversational memory
- `graph_voice_assistant.py` — `ZepGraphAgent` with knowledge-graph memory

## 7. Run the tests

Mock-based tests (no API keys needed):

```bash
make test
```

Live integration test (requires only `ZEP_API_KEY` — no LiveKit server or LLM
key). It drives the agent's memory logic directly and verifies persistence and
cross-thread recall against real Zep:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

## Troubleshooting

- **`ZEP_API_KEY is not set`** — export the key (step 5) before running.
- **Voice example exits immediately** — confirm `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
  and `LIVEKIT_API_SECRET` are set, and that a client connects to the room.
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. The live test waits for the graph to build;
  increase the wait if your graph is large or under load.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
