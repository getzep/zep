# LangGraph agent with Zep agent memory

This notebook shows a LangGraph chatbot that uses Zep's Context Graph for agent memory. It calls `thread.get_context` and provides graph edge and node search as agent tools.

## Requirements

- Python 3.10 or later.
- Notebook runner: Jupyter Notebook, installed separately.
- Packages: `zep-cloud>=4.0.0,<5`, `langchain-openai`, `langgraph`, `ipywidgets`, and `python-dotenv`.
- Environment variables: `ZEP_API_KEY` and `OPENAI_API_KEY`. The notebook sets `LANGCHAIN_TRACING_V2` to `false` and `LANGCHAIN_PROJECT` to `Zep LangGraph Tutorial`. Set `LANGSMITH_API_KEY` only if you enable LangSmith tracing.

## Run

From the repository root, install the packages and open the notebook:

```bash
pip install "zep-cloud>=4.0.0,<5" langchain-openai langgraph ipywidgets python-dotenv
jupyter notebook examples/python/langgraph-agent/agent.ipynb
```

## What the example does

- Defines graph search tools for facts and node summaries.
- Builds a chatbot that gets thread context, calls `ChatOpenAI`, saves each user and assistant turn, and trims local message state.
- Connects the chatbot and tool node in a `StateGraph` and uses `MemorySaver` for local conversation state.
- Creates a Zep user and thread. The server returns UUIDs for the user, thread, and graph.
- Sends sample messages, waits for the thread context, and asks follow-up questions about the conversation, the user's dog, and the user's job.
