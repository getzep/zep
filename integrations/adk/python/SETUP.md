# Set up the Google ADK integration

## Requirements

- Python 3.11 or later
- `google-adk>=1.19.0,<3`
- `zep-cloud>=4.0.0,<5`
- A Zep Cloud API key
- A Google API key to run the Gemini example

Create an API key in the [Zep app](https://app.getzep.com). Set the keys in
your shell:

```bash
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

## Install

Install the published package:

```bash
python -m pip install zep-adk
```

To install from this repository, run these commands from
`integrations/adk/python`:

```bash
make install
```

## Run the example

The example creates a Zep user and thread. It stores their generated UUIDs in
ADK session state and then runs an agent.

```bash
python examples/basic_agent.py
```

The example uses `ZepContextTool`, which adds retrieved context to the model's
system instruction. Use this path only when all stored content is trusted and
application-authored. See the README for model-callable search and the ADK
memory service.

## Run tests

Run unit and live tests with:

```bash
make test
```

The Zep live test requires `ZEP_API_KEY`. The Gemini agent test also requires
`GOOGLE_API_KEY`; pytest skips that test when the key is not set.

Run the remaining package checks with:

```bash
make lint
make type-check
make build
```
