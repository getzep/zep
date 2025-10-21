# Agent Memory Full Example

A complete example of a Zep agent memory implementation with a simple OpenAI chatbot. Neatly organized into a locally run Streamlit experimentation dashboard.

## Setup Instructions

### 1. Create Virtual Environment and Install Dependencies

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate

# Install required packages
pip install -r requirements.txt
```

### 2. Create ENV File and Set API Keys

Create a `.env` file in the project root directory with your API keys:

```bash
ZEP_API_KEY=your_zep_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

**Getting API Keys:**

- **Zep Account & API Key:**
  - Create a Zep account at [https://app.getzep.com](https://app.getzep.com)
  - Generate an API key from your Zep dashboard

- **OpenAI Account & API Key:**
  - Create an OpenAI account at [https://platform.openai.com](https://platform.openai.com)
  - Generate an API key from your OpenAI account settings

### 3. Run the Streamlit Application

```bash
streamlit run ui.py
```

Navigate to the localhost URL displayed in your terminal (typically `http://localhost:8501`).

## Additional/Extra Steps

### Experiment with Different Context Retrieval Methods

Zep offers multiple methods for retrieving context from memory. You can quickly experiment with these methods by modifying the definition of the `on_receive_message` function in the `agents.py` file. We recommend experimenting with different approaches:

- Learn about our high-level/easiest to use retrieval methods: [https://help.getzep.com/retrieving-memory](https://help.getzep.com/retrieving-memory)
- Customize your context block using our lower level graph search for customizable results: [https://help.getzep.com/cookbook/customize-your-context-block](https://help.getzep.com/cookbook/customize-your-context-block)

### Pre-populate a User Graph with Conversational and Business Data

To quickly test the application with a rich knowledge graph, you can pre-populate a user with conversational data and structured JSON data:

```bash
cd pre-populate-memories
python populate-memories.py
```

This script will:
- Create a test user with ID `John-1234`
- Add 25 conversation threads with realistic dialogue
- Add 10 structured JSON data pieces (venues, artists, transportation info, etc.)
- Build a comprehensive knowledge graph that you can immediately test with

The populated graph will contain information about concert venues, music preferences, transportation options, and more - allowing you to see how Zep retrieves and contextualizes information across conversations.

## What This Example Demonstrates

- **Graph-based memory retrieval** using Zep's graph search with edges scope
- **Custom context block construction** from graph facts
- **Side-by-side comparison** of agent responses with and without Zep memory
- **Latency tracking** for Zep retrieval and LLM first token
- **Multi-user support** with user selection and thread management
- **Streaming responses** with real-time updates
