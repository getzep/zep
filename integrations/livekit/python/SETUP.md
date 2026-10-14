# Setup

## Requirements

- Python 3.11 or later
- Zep API key
- OpenAI API key for the voice examples
- LiveKit URL, API key, and API secret for the voice examples
- `zep-cloud>=4.0.0,<5`

## Install

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/livekit/python
make install
```

## Configure

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
export LIVEKIT_URL="wss://your-project.livekit.cloud"
export LIVEKIT_API_KEY="your-livekit-api-key"
export LIVEKIT_API_SECRET="your-livekit-api-secret"
```

Create a Zep user and thread with `create_user` and `create_thread` before you
start the agent. Save the returned UUIDs in your application database. Set
`ZEP_USER_UUID` and `ZEP_THREAD_UUID` for the thread example. Set
`ZEP_GRAPH_UUID` for the graph example. You can also set `ZEP_TEMPLATE_UUID`
to use a saved Zep context template with the graph agent.

## Run an example

```bash
uv run python examples/voice_assistant.py dev
```

To run the graph example:

```bash
uv run python examples/graph_voice_assistant.py dev
```

## Run checks

Run formatting, lint, type, and unit checks:

```bash
make ci
```

Run the live Zep test:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

The live test needs only `ZEP_API_KEY`. It creates users and threads with the
V4 SDK and deletes them when the test ends. It does not need a LiveKit server
or an LLM provider.
