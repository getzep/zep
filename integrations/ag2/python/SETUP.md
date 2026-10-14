# Setup

This guide prepares the AG2 integration and its examples.

## Get a Zep API key

Create a Zep account at [getzep.com](https://www.getzep.com). Open the
[Zep dashboard](https://app.getzep.com), select a project, and copy its API
key.

## Install

```bash
pip install zep-ag2
```

The package requires Python 3.11+, AG2 0.9 or later, and Zep Cloud SDK 4.x.

For local development, run:

```bash
make install
```

## Configure

Set `ZEP_API_KEY` to a key for your Zep project. Set `OPENAI_API_KEY` for
examples that call an OpenAI model.

```bash
export ZEP_API_KEY="your-zep-cloud-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

## Run an example

```bash
python examples/ag2_basic.py
```

The examples create Zep users and threads through the V4 API. They use the
server-generated UUIDs returned by those create calls.

- `ag2_basic.py` attaches a memory manager to an AG2 agent.
- `ag2_graph.py` uses a shared Context Graph.
- `ag2_tools_search.py` registers a graph search tool.
- `ag2_tools_full.py` registers all memory tools.
- `manual_test.py` runs a live memory check.

See [README.md](README.md) for the API reference and tool options.
