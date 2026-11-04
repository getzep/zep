# AutoGen agent with Zep agent memory

This notebook shows an AutoGen `ConversableAgent` that uses Zep's Context Graph for agent memory. It stores chat turns in a thread, adds `thread.get_context` results to the system message, and searches graph edges.

## Requirements

- Python 3.10 or later.
- Notebook runner: Jupyter Notebook, installed separately.
- Packages: `autogen[openai]`, `zep-cloud>=4.0.0,<5`, and `python-dotenv`.
- Environment variables: `OPENAI_API_KEY` and `ZEP_API_KEY`. The notebook loads these from the environment or a `.env` file.

## Run

From the repository root, install the packages and open the notebook:

```bash
pip install "autogen[openai]" "zep-cloud>=4.0.0,<5" python-dotenv
jupyter notebook examples/python/autogen-agent/agent.ipynb
```

## What the example does

- Creates a Zep user and thread. The server returns UUIDs for the user, thread, and graph.
- Adds a prior conversation to the thread and waits for the ingestion task.
- Lists facts from the user's graph.
- Extends `ConversableAgent` to save user and assistant messages and add thread context to the agent's system message.
- Runs a three-turn conversation between CareBot and Cathy, then searches graph edges for facts about Cathy's family.
