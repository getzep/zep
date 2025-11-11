# User Summary Instructions Example

A complete example demonstrating Zep's **User Summary Instructions** feature through an interactive real estate sales agent chatbot. This example shows how to define what information should always be available to your agent—regardless of conversational context.

## What Are User Summary Instructions?

User Summary Instructions let you define specific questions or directives that your agent should always have answers to. While Zep's semantic search retrieves contextually relevant information, User Summary Instructions ensure that **critical facts are always included** in the context block.

### The Real Estate Agent Use Case

Consider a real estate sales agent: a buyer's budget matters on *every* interaction—not just when they explicitly mention money. Similarly, bedroom requirements, location preferences, and must-have features should persist across all conversations.

This example demonstrates how to configure User Summary Instructions to address questions like:
- "What is the user's budget or price range for purchasing a home?"
- "How many bedrooms does the user need and why?"
- "What are the user's must-have features in a home?"
- "What locations or school districts is the user prioritizing?"

As new conversations arrive, **Zep continuously refines the user summary** to answer these questions, ensuring your agent always works with current information.

## What This Example Includes

This example provides:
- **Pre-configured User Summary Instructions** for a real estate agent
- **Sample data** with realistic home buyer preferences and conversations
- **RealEstateSalesAgent** that uses `thread.get_user_context()` to retrieve the custom user summary
- **Interactive dashboard** to test and compare responses with/without Zep memory
- **Side-by-side comparison** showing how User Summary Instructions improve agent responses
- **Latency tracking** for Zep retrieval and LLM response times

## Setup

### 1. Install Dependencies

```bash
# Create and activate a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On macOS/Linux
# venv\Scripts\activate   # On Windows

# Install required packages
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the project root with your API keys:

```bash
ZEP_API_KEY=your_zep_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

**Get your API keys:**
- **Zep**: Sign up at [app.getzep.com](https://app.getzep.com) and generate an API key
- **OpenAI**: Get your key from [platform.openai.com](https://platform.openai.com)

### 3. Enable User Summary in Zep Project Settings

**Important**: For the user summary to be automatically included in the context block returned by `get_user_context()`, you need to enable this setting in your Zep project:

1. Go to the [Zep web app](https://app.getzep.com)
2. Navigate to your project's **Settings** page
3. Enable **"Include User Summary in Context"** (or similar setting name)

This ensures that the custom user summary generated from your User Summary Instructions will be automatically included in every context retrieval.

### 4. Ingest Sample Data

Run the ingestion script to configure User Summary Instructions and populate Zep with sample data:

```bash
python zep_ingest.py
```

This script:
1. **Configures User Summary Instructions** for the real estate domain
2. Creates test users from `data/user_data.json`
3. Ingests sample conversations about home buying
4. Adds structured preference data to Zep's knowledge graph

The sample data represents realistic home buyer scenarios with budgets, bedroom requirements, location preferences, and must-have features.

### 5. Launch the Dashboard

```bash
streamlit run ui.py
```

The dashboard will open in your browser at `http://localhost:8501`.

## How It Works

### 1. User Summary Instructions Configuration

The ingestion script (`zep_ingest.py`) configures User Summary Instructions that define what information should always be available:

```python
zep_client.user.add_user_summary_instructions(
    instructions=[
        UserInstruction(
            name="bedroom_requirements",
            text="How many bedrooms does the user need and why?"
        ),
        UserInstruction(
            name="price_range",
            text="What is the user's budget or price range for purchasing a home?"
        ),
        # Additional instructions for location, features, etc.
    ]
)
```

### 2. User Summary Generation

As Zep processes conversations and structured data, it generates a user summary that answers these questions:

> *The user requires 3-4 bedrooms for their growing family. Their budget is set between $300,000 and $410,000. They prioritize homes near top-rated schools, with a preference for the Riverside school district. Must-have features include home office space for remote work, a 2-car garage, and an updated kitchen.*

**This summary automatically updates as new information arrives**, ensuring the agent always works with current preferences.

### 3. Retrieving Context with User Summary

The `RealEstateSalesAgent` retrieves context using `get_user_context()`, which automatically includes the custom user summary:

```python
results = await zep_client.thread.get_user_context(
    thread_id=thread_id,
    mode="basic"
)
context_block = results.context  # Includes the user summary

system_prompt = (
    "You are a helpful real estate sales agent. "
    "Use the context provided to personalize your recommendations.\n\n"
    f"{context_block}"
)
```

### 4. See the Difference

The dashboard lets you compare responses **with** and **without** Zep memory side-by-side. Try messages like:
- "Show me some houses in Palo Alto" (minimal context)
- "What's my budget again?" (testing memory)
- "I changed my mind, I need 5 bedrooms now" (dynamic updates)

With User Summary Instructions, the agent responds with full context—even when the user provides minimal information.

## Using the Dashboard

### Dashboard Controls

**Agent Selector** (in sidebar)
- Default agent: `RealEstateSalesAgent` (uses `thread.get_user_context()`)
- Add your own custom agents by copying and modifying the existing agent
- All agent classes with an `on_receive_message` method automatically appear in this dropdown

**Display Mode** (in sidebar)
- **Both Responses**: Side-by-side comparison of responses with/without Zep
- **With Zep**: Show only the response using Zep memory (includes user summary)
- **Without Zep**: Show only the response without Zep memory

**User Management** (in sidebar)
- Select existing users from the dropdown to see their personalized context
- Click "➕ New User" to create a new user and test from scratch

**Thread Management** (in sidebar)
- Click "➕ New Thread" to start a new conversation
- Click on any existing thread to continue a previous conversation

### What Gets Displayed

When you send a message:
- **User message** appears right-aligned in gray
- **Context block** (with Zep) shows the retrieved memory including the user summary
- **AI responses** appear in boxes below, with latency metrics
- **Side-by-side view** clearly demonstrates the impact of User Summary Instructions

## Key Benefits Demonstrated

### 1. Conversation Starts
When threads begin with greetings or minimal context ("Show me some homes"), there's little for semantic search alone to work with. The user summary ensures the agent starts informed.

### 2. Topic Shifts
Conversations naturally drift between topics (financing, location, features). The user summary keeps core preferences accessible regardless of conversational flow.

### 3. Persistent Memory
As conversations span days or weeks, the user summary acts as a memory anchor. The agent doesn't need to re-learn preferences—they're always in context.

## Customizing for Your Domain

This example is a template you can adapt to your use case:

1. **Copy this folder** to a new location

2. **Define your domain's User Summary Instructions** in `zep_ingest.py`:
   - Replace real estate questions with your domain-specific needs
   - Examples: customer support (issue history, account type), sales (deal stage, budget), healthcare (medical history, preferences)

3. **Replace data files** in `data/` with your domain-specific data:
   - `user_data.json`: User profile info and structured preferences for your use case
   - `conversations.json`: Sample conversation threads relevant to your domain

4. **Update the agent** in `agents.py`:
   - Rename `RealEstateSalesAgent` to match your domain (e.g., `CustomerSupportAgent`)
   - Modify the system prompt for your domain/persona
   - Adjust context formatting if needed

5. **Run ingestion** and **test in the dashboard**

### Creating Additional Agents

You can add multiple agent variants by copying the existing agent class:
- Different personas (friendly vs. professional)
- Different retrieval strategies (basic vs. advanced mode)
- Domain-specific formatting

Your new agents will **automatically appear in the dropdown**—no other code changes needed!

## Project Structure

```
user-summary-instructions-example/
├── README.md              # This file
├── requirements.txt       # Python dependencies
├── .env                  # Your API keys (create this)
│
├── ui.py                 # Streamlit dashboard
├── agents.py             # RealEstateSalesAgent implementation
├── zep_ingest.py         # Configures User Summary Instructions and ingests data
│
├── assets/               # UI assets
│   └── zep-logo.png
│
└── data/                 # Sample real estate data
    ├── conversations.json  # Sample home buyer conversations
    └── user_data.json      # User profiles and preferences
```

## Learn More

- [User Summary Instructions Documentation](https://help.getzep.com/users#user-summary-instructions)
- [Retrieving Memory with Zep](https://help.getzep.com/retrieving-memory)
- [Customizing Context Blocks](https://help.getzep.com/cookbook/customize-your-context-block)
- [Zep Documentation](https://help.getzep.com)

## Troubleshooting

**Import errors**
- Activate your virtual environment and run `pip install -r requirements.txt`

**API key errors**
- Check that `.env` file exists with valid `ZEP_API_KEY` and `OPENAI_API_KEY`

**No context being retrieved**
- Run `python zep_ingest.py` to configure User Summary Instructions and populate Zep with sample data
- Make sure you're selecting a user that has data in Zep

**User summary not appearing in context**
- **Check project settings**: Go to your project's Settings page in the [Zep web app](https://app.getzep.com) and ensure "Include User Summary in Context" is enabled
- Verify that User Summary Instructions were successfully configured (check the ingestion script output)
- Ensure the selected user has conversations/data in Zep that Zep can use to generate the summary
- Give Zep a few moments after ingestion to generate the user summary from the data

**Ingestion fails**
- Verify `data/conversations.json` and `data/user_data.json` exist
- Check your Zep API key is valid
