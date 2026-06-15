# Getting started with Zep

Install, initialize, and run the minimal loop. All examples are Zep V3.

## Install

```bash
# Python
pip install zep-cloud          # or: uv pip install zep-cloud

# TypeScript
npm install @getzep/zep-cloud  # yarn add / pnpm install also work

# Go
go get github.com/getzep/zep-go/v3
```

## Initialize the client

Create **one** client and reuse it across the app — the SDK keeps an HTTP connection pool, and creating a client per request hurts latency. The API key comes from the `ZEP_API_KEY` environment variable.

```python
from zep_cloud.client import Zep
client = Zep(api_key=API_KEY)
```

```typescript
import { ZepClient } from "@getzep/zep-cloud";
const client = new ZepClient({ apiKey: API_KEY });
```

```go
import (
    "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)
client := zepclient.NewClient(option.WithAPIKey(apiKey))
```

## The minimal loop

The core integration is: **create a user → create a thread → add messages → get context**. Do this with defaults first; add nothing else until you've measured a gap.

### 1. Create a user (once per user)

Set the Zep `user_id` to your internal user ID. Always pass at least a first name — ideally last name and email — so Zep resolves the user against references in the data.

```python
user = client.user.add(
    user_id="your_internal_user_id",
    email="jane.smith@example.com",
    first_name="Jane",
    last_name="Smith",
)
```

Backfill existing users with a one-time loop calling `user.add` for each. Creating a user implicitly creates their user graph.

### 2. Create a thread (once per conversation)

```python
import uuid
thread_id = uuid.uuid4().hex
client.thread.create(thread_id=thread_id, user_id=user_id)
```

### 3. Add messages and retrieve context (per turn)

```python
from zep_cloud.types import Message
from datetime import datetime, timezone

# Add the incoming user message
client.thread.add_messages(
    thread_id,
    messages=[Message(
        name="Jane Smith",
        role="user",
        content="Who was Octavia Butler?",
        created_at=datetime.now(timezone.utc).isoformat(),
    )],
)

# Retrieve the Context Block before calling your LLM
user_context = client.thread.get_user_context(thread_id=thread_id)
context_block = user_context.context   # drop this string into your prompt

# ... call your LLM with context_block in the system/context section ...

# Persist the assistant's reply so it becomes memory too
client.thread.add_messages(
    thread_id,
    messages=[Message(
        name="AI Assistant",
        role="assistant",
        content="Octavia Butler was an American science fiction author...",
        created_at=datetime.now(timezone.utc).isoformat(),
    )],
)
```

`thread.add_messages` does two things in one call: appends to thread history **and** ingests into the user graph. `get_user_context` returns context from the *entire* user graph; the thread only determines relevance.

TypeScript/Go mirror these names: `client.thread.addMessages`, `client.thread.getUserContext`; `client.Thread.AddMessages`, `client.Thread.GetUserContext`.

## Adding messages — details

`thread.add_messages(thread_id, messages=[...], return_context=False, ignore_roles=None)`

- `Message` fields: `role` (required: `user`/`assistant`/`system`/`tool`/`function`/`norole`), `content` (required, ≤4,096 chars), `name` (real speaker name — important for graph construction), `created_at` (RFC3339, recommended), `metadata` (≤10 scalar key/values).
- Limits: **max 30 messages per call**, **4,096 chars per message**. Split or use the Batch API beyond that.
- `return_context=True` returns the Context Block in the same response — a latency win that avoids a second round trip.
- `ignore_roles=["assistant"]` keeps a role in thread history but excludes it from graph ingestion.

## Adding business data / documents / JSON — `graph.add`

For anything that isn't a live conversation, ingest directly into a user graph (`user_id=`) or standalone graph (`graph_id=`):

```python
import json

# Text (documents, wikis, handbooks)
client.graph.add(user_id="user123", type="text",
                 data="The user is an avid fan of Eric Clapton")

# JSON (structured business data, API responses)
client.graph.add(user_id="user123", type="json",
                 data=json.dumps({"name": "Eric Clapton", "age": 78, "genre": "Rock"}))

# Message (conversational data not part of a thread — e.g. imported emails)
client.graph.add(user_id="user123", type="message",
                 data="Paul (user): I went to an Eric Clapton concert last night")
```

- **10,000 character limit** per `graph.add` call — chunk larger documents.
- Optional `created_at` (when the data was originally created — important for temporal accuracy) and `metadata` (≤10 scalar keys).
- Adds to the **same graph** are processed sequentially. For large datasets use the Batch API.

### JSON best practices

Bad JSON produces a sparse graph. Before ingesting JSON:
- **Split large objects** into pieces of ~3–4 properties; duplicate identifying fields (`id`, `name`, `description`) across pieces.
- **Flatten** nesting deeper than 3–4 levels while preserving context.
- Make each piece **understandable in isolation** (descriptive keys, include descriptions).
- Ensure each piece represents **one unified entity**; split objects that bundle several.

## Batch ingestion (Enterprise)

For historical backfills, document collections, and migrations — faster than per-item calls and isolated from live traffic.

```python
batch = client.batch.create(metadata={"description": "support backfill"})
client.batch.add(batch.batch_id, items=[...])   # up to 500 items/call, 50,000/batch
client.batch.process(batch.batch_id)            # async
# poll until terminal
while (s := client.batch.get(batch.batch_id)).status not in ("succeeded","partial","failed","invalid"):
    time.sleep(5)
```

Item types: `graph_episode` (= `graph.add`) and `thread_message` (= one message in `add_messages`). Note: user summary instructions don't apply to Batch-ingested data.

## Ingestion is asynchronous

Graph construction runs in the background — on the order of seconds per message. A fact you just added is not instantly retrievable. Design for eventual availability; when you must confirm processing, inspect the returned message/episode UUIDs and poll their status (or, in the eval harness, allow time before querying).
