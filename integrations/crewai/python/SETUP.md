# Setup guide

## Requirements

- Python 3.11 or later
- A Zep API key
- An OpenAI API key to run the live CrewAI test

## Install from this repository

```bash
cd integrations/crewai/python
make install
```

This installs `zep-cloud>=4.0.0,<5` and the development dependencies.

## Configure credentials

```bash
export ZEP_API_KEY="..."
export OPENAI_API_KEY="..."
```

Do not commit API keys. The package's examples read them from the environment.

## Run an example

```bash
uv run python examples/simple_example.py
```

The examples create V4 users, threads, and graphs. They use returned `uuid_` values.

## Run checks

```bash
make format
make lint
make type-check
make test
```

Run the live test with:

```bash
uv run pytest tests/test_integration.py -v -s -m integration
```

The live test creates temporary Zep resources and deletes the user after it finishes.
