# Setup

## Get a Zep API key

Create a Zep Cloud account at [app.getzep.com](https://app.getzep.com). Create an API key and keep it in a secure environment variable.

## Install the package

Use Python 3.11 or later. Install the integration and configure the API key:

```bash
python -m pip install zep-pydantic-ai
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

The package uses `zep-cloud>=4.0.0,<5` and `pydantic-ai>=1.107,<2`.

## Run the example

```bash
cd integrations/pydantic-ai/python
python examples/basic_agent.py
```

The example creates a user and thread with server-generated UUIDs, runs the agent, then deletes the thread and user.

## Run tests

Install the development dependencies and run the unit and static checks:

```bash
make install
make lint
ruff format --check .
make type-check
make test
```

The live lifecycle test requires `ZEP_API_KEY` and `OPENAI_API_KEY`:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

The live test creates a user and two threads through the V4 SDK helpers. It checks message persistence and cross-thread recall. It deletes the test threads and user at the end.
