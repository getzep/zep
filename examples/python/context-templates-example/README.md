# Context templates example

This example is a Streamlit chat dashboard whose agent gets its context from a Zep
context template. `set-context-templates.py` creates the template
`requirements-and-preferences-1`. The template returns the user summary, up to four
`HAS_REQUIREMENT` and `PREFERS_NEIGHBORHOOD` facts, up to three entities, and up to
two episodes. The chat agent finds the template UUID by its name and uses the
template when it gets context for each message.

## Requirements

- Python 3.10 or later.
- Packages: `openai==3.13.0`, `python-dotenv==1.2.2`, `streamlit==1.54.0`, and `zep-cloud>=4.0.0,<5`.
- `ZEP_API_KEY` for Zep and `OPENAI_API_KEY` for the chat agent. The scripts load these values from the environment or `.env`.

## Run

Run these commands from `examples/python/context-templates-example/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `ZEP_API_KEY` and `OPENAI_API_KEY` in `.env`. Create the context template:

```bash
python set-context-templates.py
```

To create the sample user, set the real estate ontology on its graph, and add the conversation data to Zep, run:

```bash
python zep_ingest.py
```

Start the Streamlit app:

```bash
streamlit run ui.py
```

Warning: The setup creates a project context template and a graph ontology. Use a disposable project key.

## What the example does

- `set-context-templates.py` creates a template with a user summary, up to four `HAS_REQUIREMENT` and `PREFERS_NEIGHBORHOOD` edges, up to three entities, and up to two episodes.
- `ontology.py` defines real estate entity and edge types. `zep_ingest.py` sets these types on the graph for the new user.
- `data/` contains Jane Doe's profile, house-search records, and home-buying conversations. `zep_ingest.py` creates a user from `user.json` and adds messages from `conversations.json` to threads. Its graph episode upload for `user_data.json` is commented out.
- `agents.py` stores user and assistant messages in the thread, gets context with the template UUID, and streams a response from OpenAI.

## Project structure

```
context-templates-example/
├── .env.example
├── README.md
├── agents.py
├── assets/
│   └── zep-logo.png
├── data/
│   ├── conversations.json
│   ├── user.json
│   └── user_data.json
├── ontology.py
├── requirements.txt
├── set-context-templates.py
├── ui.py
└── zep_ingest.py
```

## Learn more

- [Context templates](https://help.getzep.com/context-templates)
