# Python Context Graph examples

These scripts show Context Graph operations with the Zep v4 Python SDK. They create graphs and user threads, add and search episodes, inspect graph records, and set a project ontology.

## Requirements

- Python 3.10 or later.
- Packages: `zep-cloud>=4.0.0,<5`, `python-dotenv==1.2.2`, and `pydantic==2.13.5`, as listed in `examples/python/requirements.txt`.
- Environment variable: `ZEP_API_KEY`. The scripts load it from the environment or a `.env` file.

Warning: `entity_types.py` sets the ontology for the whole project; use a disposable project key.

## Run

From the repository root, install the packages:

```bash
python -m pip install "zep-cloud>=4.0.0,<5" "python-dotenv==1.2.2" "pydantic==2.13.5"
```

Run one example script at a time:

- `python examples/python/graph_example/graph_example.py`
- `python examples/python/graph_example/user_graph_example.py`
- `python examples/python/graph_example/tickets_example.py`
- `python examples/python/graph_example/entity_types.py`

`conversations.py` supplies sample messages to `user_graph_example.py`.

## What the example does

- `graph_example.py` creates a graph, adds text and JSON episodes, lists episodes, nodes, and edges, reads an episode, and searches graph edges.
- `user_graph_example.py` creates a user and thread with server-returned UUIDs, adds sample messages, calls `thread.get_context`, and runs graph searches.
- `tickets_example.py` creates a user and adds ticket, purchase, and waitlist events as message and JSON episodes.
- `entity_types.py` sets the project ontology, adds travel messages, and searches for edges with the `TRAVELING_TO` type.
