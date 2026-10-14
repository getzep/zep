# LiveKit and Zep example

This example uses the Zep V4 Python SDK. Install the package dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a Zep user and thread with the package `create_user` and
`create_thread` helpers. Save the returned UUIDs. Set `ZEP_USER_UUID` and
`ZEP_THREAD_UUID` for the thread agent. Set `ZEP_GRAPH_UUID` for the graph
agent.

Set the provider and LiveKit values in `.env`:

```text
ZEP_API_KEY=your-zep-api-key
ZEP_USER_UUID=your-user-uuid
ZEP_THREAD_UUID=your-thread-uuid
ZEP_GRAPH_UUID=your-graph-uuid
# Optional, for the graph agent:
ZEP_TEMPLATE_UUID=your-template-uuid
OPENAI_API_KEY=your-openai-api-key
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
```

Run the thread agent:

```bash
python zep_voice_agent.py dev
```

Run the graph agent:

```bash
python zep_graph_voice_agent.py dev
```

To load the sample profile and conversations into a user's graph, run:

```bash
python populate_memory.py
```

The population script creates new threads. Save each returned thread UUID if
you will use it in an agent.
