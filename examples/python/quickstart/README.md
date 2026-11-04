# Python quickstart with Zep Context Graph

This notebook uses the Zep Python SDK and the OpenAI client to add support messages and business data to a user's Context Graph. It retrieves thread context for a new support question and separately searches graph edges.

## Requirements

- Python 3.10 or later.
- Notebook runner: Jupyter Notebook, installed separately.
- Packages: `zep-cloud>=4.0.0,<5`, `openai`, `rich`, and `python-dotenv`.
- Environment variables: `ZEP_API_KEY` and `OPENAI_API_KEY`. The notebook loads these from the environment or a `.env` file.

## Run

From the repository root, install the packages and open the notebook:

```bash
pip install "zep-cloud>=4.0.0,<5" openai rich python-dotenv
jupyter notebook examples/python/quickstart/quickstart.ipynb
```

## What the example does

- Creates Emily as a Zep user and creates a thread. The server returns UUIDs for the user, thread, and graph.
- Adds chat history to the thread and adds support cases, transactions, and account status as graph episodes.
- Waits for graph processing, then reads graph edges, thread context, thread messages, and search results.
- Creates another thread for a login question and gets its context with `thread.get_context`.
- Sends the context and question to the OpenAI chat completion API and prints the response.
