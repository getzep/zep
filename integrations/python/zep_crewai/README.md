# Zep CrewAI Integration Tutorial

Learn how to add persistent memory to your CrewAI agents using Zep's powerful memory platform.

## Installation

Install the Zep CrewAI integration package:

```bash
pip install zep-crewai
```

## Setup

### 1. Get Your API Key

Sign up at [Zep Cloud](https://app.getzep.com) and get your API key.

### 2. Set Environment Variable

```bash
export ZEP_API_KEY="your-zep-api-key"
```

## Basic Usage

### 1. Initialize Zep Client

```python
import os
from zep_cloud.client import Zep

# Initialize Zep client
zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))
```

### 2. Create User and Thread

**Important**: You must create a user and thread in Zep before using ZepStorage.

```python
# Create a user 
user_id = "john_doe_123"
zep_client.user.add(
    user_id=user_id,
    first_name="John",
    last_name="Doe",
    email="john.doe@example.com"
)

# Create a thread 
thread_id = "project_alpha_456"
zep_client.thread.create(
    user_id=user_id,
    thread_id=thread_id
)
```

### 3. Initialize ZepStorage

```python
from zep_crewai import ZepStorage
from crewai.memory.external.external_memory import ExternalMemory

# Create storage for your project
zep_storage = ZepStorage(
    client=zep_client,
    user_id=user_id,   
    thread_id=thread_id
)

# Wrap in CrewAI's external memory
external_memory = ExternalMemory(storage=zep_storage)
```

### 4. Create Crew with Persistent Memory

```python
from crewai import Agent, Crew, Task, Process

# Create your agents
research_agent = Agent(
    role='Research Analyst',
    goal='Analyze market trends and provide insights',
    backstory='You are an expert at finding and analyzing market data...',
)

# Create crew with Zep memory
crew = Crew(
    agents=[research_agent],
    tasks=[...],
    external_memory=external_memory,  # This enables the crew to search Zep
    process=Process.sequential,
)

# Run your crew - memories will be automatically saved and retrieved
result = crew.kickoff()
```

## How Memory Works

Zep stores different types of content using metadata-based routing.

### Messages (Conversation Context)
Stored in Zep threads for conversation history:

```python
external_memory.save(
    "I need help planning a business trip to New York",
    metadata={"type": "message", "role": "user", "name": "John Doe"}
)

external_memory.save(
    "I'd be happy to help you plan your trip!",
    metadata={"type": "message", "role": "assistant", "name": "Travel Agent"}
)
```

### Structured Data
Added as episodes to the user knowledge graph in Zep:

```python
# JSON data
external_memory.save(
    '{"destination": "New York", "duration": "3 days", "budget": 2000}',
    metadata={"type": "json"}
)

# Text facts and insights
external_memory.save(
    "User prefers mid-range hotels with business amenities",
    metadata={"type": "text"}
)
```

### Automatic Memory Retrieval

CrewAI automatically searches your memories when agents need context:

```python
# When agents run, they automatically get relevant context from Zep
results = crew.kickoff()

# You can also search manually
memory_results = zep_storage.search("hotel preferences", limit=5)
for result in memory_results:
    print(result['memory'])
```

## Complete Example

For a full working example, check out [`examples/simple_example.py`](examples/simple_example.py) in this repository. This example demonstrates:

- Setting up Zep user and thread
- Saving different types of memory (messages, JSON data, text)
- Creating CrewAI agents with access to Zep memory
- Automatic context retrieval during agent execution

## Requirements

- Python 3.10+
- `zep-cloud>=3.0.0rc1`
- `crewai>=0.80.0`