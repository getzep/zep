# OpenAI Agents SDK with Zep Memory Example

This example demonstrates how to integrate Zep memory with an agent built using the OpenAI Agents SDK. It creates an assistant that can remember previous conversations using Zep's asynchronous memory capabilities.

## Features

- Integration of OpenAI Agents SDK with Zep's memory system
- Persistent memory across conversations
- Memory search functionality to retrieve relevant information
- User management with metadata support
- Interactive chat mode
- Session management with optional reuse

## Prerequisites

- Python 3.10+
- uv or poetry for dependency management
- OpenAI API key
- Zep Cloud API key

## Installation

1. Clone the repository:

```bash
git clone https://github.com/getzep/zep-python.git
cd zep-python/examples/openai-agents-sdk
```

2. Install the required dependencies using uv or poetry:

Using uv:

```bash
uv sync
```

Or using poetry:

```bash
poetry install
```

3. Set up environment variables:

Create a `.env` file in the project directory with the following content:

```
OPENAI_API_KEY=your_openai_api_key
ZEP_API_KEY=your_zep_api_key
```

4. Enter a `uv` or `poetry` shell:

```bash
uv shell
```

Or using poetry:

```bash
poetry shell
```

## Usage

### Basic Usage

Run the example with default settings:

```bash
python openai_agents_sdk_example.py
```

### Interactive Mode

Run in interactive mode for continuous conversation:

```bash
python openai_agents_sdk_example.py --interactive
```

### User Information

Provide user information for better memory management:

```bash
python openai_agents_sdk_example.py --username user123 --email user@example.com --firstname John --lastname Doe
```

### Session Management

Specify a session ID to reuse the same session:

```bash
python openai_agents_sdk_example.py --username user123 --session my-session-id
```

Note: By default, a timestamp will be appended to the session ID to create a new session each time.

## How It Works

The example consists of two main components:

1. **AsyncZepMemoryManager**: Handles memory operations using the AsyncZep client.

   - Initializes the AsyncZep client
   - Creates or retrieves users
   - Manages memory sessions
   - Adds messages to memory
   - Retrieves memory context
   - Searches memory for relevant information

2. **AsyncZepMemoryAgent**: Integrates the memory manager with the OpenAI Agents SDK.
   - Creates an agent with memory-related tools
   - Processes and stores messages
   - Handles chat interactions

## Available Tools

The agent is equipped with the following tools:

- **search_memory**: Searches Zep memory for relevant facts based on a query
- **get_weather**: A simple example tool that returns weather information for a given city

## Example Conversation

When you run the example, you can have conversations like:

```
User: My name is Alice and I live in New York.
Agent: Nice to meet you, Alice! I'll remember that you live in New York. How can I help you today?

User: What's the weather like?
Agent: Based on your location in New York, the current weather is...

User: Where do I live again?
Agent: You mentioned that you live in New York.
```
