# LangGraph CLI Agent

This is a simple CLI agent built with LangGraph.js that can answer questions and use tools to search for information. It also integrates with Zep for memory persistence.

## Prerequisites

- Node.js 18+ installed
- OpenAI API key
- Tavily API key (for search functionality)
- Zep API key (for memory persistence)

## Setup

1. Install dependencies:

```bash
npm install
```

2. Create a `.env` file in this directory with your API keys:

```
OPENAI_API_KEY=your_openai_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
ZEP_API_KEY=your_zep_api_key_here
```

## Running the Agent

To start the CLI agent, run:

```bash
npm start
```

### Command-Line Options

The agent supports various command-line options through Commander.js:

```bash
# Get help
npm start -- --help

# Basic usage with user ID
npm start -- --userId john_doe

# Using kebab-case format
npm start -- --user-id john_doe --thread-id vacation_planning

# Customize the system message
npm start -- --system-message "You are a travel assistant helping with vacation planning."

# Enable debug mode
npm start -- --debug
```

Available options:

| Option                       | Description                                |
| ---------------------------- | ------------------------------------------ |
| `--userId <id>`              | User ID to associate with the conversation |
| `--user-id <id>`             | Alternative format for user ID             |
| `--threadId <id>`            | Thread ID for the conversation             |
| `--thread-id <id>`           | Alternative format for thread ID           |
| `--system-message <message>` | Custom system message to use               |
| `--debug`                    | Enable debug mode with additional logging  |
| `--help`                     | Display help information                   |
| `--version`                  | Display version information                |

### Memory Features

Using the user ID and thread ID options allows you to:

- **Maintain separate conversation histories** for different users
- **Continue specific conversations** by using the same thread ID
- **Group related conversations** under the same user ID
- **Switch between different contexts** by changing the thread ID

The agent will automatically create users and threads in Zep if they don't exist, and will load previous conversation history when available.

## Features

This will launch an interactive CLI where you can chat with the agent. Type your questions and the agent will respond. The agent can:

- Answer general knowledge questions
- Search the web for information using Tavily
- Maintain context throughout the conversation
- Persist conversation history using Zep memory
- Retrieve past conversations when restarted
- Associate conversations with specific users
- Continue conversations across different threads
- Use custom system messages

Type `exit` to quit the application.

## How It Works

This agent is built using LangGraph.js, which allows for creating stateful, multi-step AI workflows as graphs. The agent:

1. Takes user input
2. Processes it through an LLM (OpenAI)
3. Decides whether to use tools or respond directly
4. If tools are needed, executes them and returns to the LLM
5. Returns the final response to the user
6. Persists all messages to Zep memory (if configured)

The graph maintains state between interactions, allowing for conversational context to be preserved. Additionally, the Zep memory integration provides long-term persistence of conversation history.

## Memory Integration

The agent uses Zep for memory persistence. When a Zep API key is provided, the agent will:

1. Create a new thread in Zep (or use an existing one)
2. Store all user and AI messages in Zep memory
3. Maintain conversation history across restarts
4. Load previous messages when restarted
5. Provide context to the agent about the previous conversation

### User and Thread Management

The agent supports specifying a user ID and thread ID via command-line arguments:

- **User ID**: Associates conversations with a specific user in Zep
- **Thread ID**: Uses a specific thread ID for the conversation

This allows for powerful use cases such as:

- **Multi-user support**: Different users can have their own conversation histories
- **Topic-based threads**: Create different threads for different topics (e.g., "travel_planning", "tech_support")
- **Conversation continuity**: Resume specific conversations by using the same thread ID
- **Organizational structure**: Group related conversations under the same user ID
- **A/B testing**: Compare different conversation flows by using different thread IDs

### Advanced Memory Features

The Zep memory integration includes several advanced features:

- **User Management**: Creates and manages users associated with conversations
- **Thread Management**: Creates and retrieves threads for persistent conversations
- **Message Retrieval**: Loads previous messages when the agent is restarted
- **Memory Search**: Can search for relevant messages based on semantic similarity
- **Context Retrieval**: Can retrieve context information about the conversation

This allows the agent to maintain context over long periods and provide more personalized responses based on past interactions.

## Customization

You can modify `agent.ts` to:

- Change the LLM model
- Add additional tools
- Adjust the graph structure
- Customize the CLI interface
- Configure memory settings

The `zep-memory.ts` file contains the Zep memory integration and can be customized to:

- Change how messages are stored and retrieved
- Modify user and thread management
- Add additional memory features
- Customize message conversion between LangChain and Zep formats

## License

MIT
