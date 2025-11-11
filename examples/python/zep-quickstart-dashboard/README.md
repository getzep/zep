# Zep Quickstart Dashboard

An interactive Streamlit dashboard for experimenting with Zep's memory features. This dashboard provides a complete UI for testing different memory retrieval methods, comparing responses with and without Zep, and managing multiple users and conversation threads.

## What This Dashboard Does

This dashboard helps you:
- **Experiment with Zep's memory features** in an interactive environment
- **Compare AI responses** with and without Zep memory side-by-side
- **Test different agent types** with automatic agent discovery - just add new agent classes and they appear in the UI
- **Manage multiple users and conversations** with full thread history
- **Track latency metrics** for Zep retrieval and LLM response times
- **Visualize memory context** to see what information Zep provides to the LLM

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

### 3. Pre-populate with Sample Data (Optional)

Run the ingestion script to create a test user and add sample data to Zep:

```bash
python zep_ingest.py
```

This creates:
- A test user with a unique ID
- Sample conversation threads
- Structured JSON data in Zep's knowledge graph

The sample data is about concerts/music preferences. You can replace the files in `data/` with your own data.

### 4. Launch the Dashboard

```bash
streamlit run ui.py
```

The dashboard will open in your browser at `http://localhost:8501`.

## Using the Dashboard

### Dashboard Controls

**Agent Selector** (in sidebar)
- Choose from available agent types defined in `agents.py`
- Default agent: `ChatAgent` (uses `thread.get_user_context()`)
- All agent classes with an `on_receive_message` method automatically appear in this dropdown
- Add your own custom agents by copying and modifying the default agent

**Display Mode** (in sidebar)
- **Both Responses**: Side-by-side comparison of responses with/without Zep
- **With Zep**: Show only the response using Zep memory
- **Without Zep**: Show only the response without Zep memory

**User Management** (in sidebar)
- Select existing users from the dropdown
- Click "➕ New User" to create a new user

**Thread Management** (in sidebar)
- Click "➕ New Thread" to start a new conversation
- Click on any existing thread to switch to it

### What Gets Displayed

When you send a message:
- **User message** appears right-aligned in gray
- **Context block** (if using Zep) shows what memory Zep retrieved
- **AI responses** appear in boxes below, with latency metrics
- **Side-by-side view** lets you compare responses with/without memory

## Customizing for Your Use Case

This dashboard is a template designed to be copied and customized. To create your own example:

1. **Copy this folder** to a new location

2. **Replace data files** in `data/` with your domain-specific data:
   - `conversations.json`: Sample conversation threads
   - `user_data.json`: Structured data for your use case (note: user profile info like name/email is defined here, not in the ingestion script)

3. **Update `zep_ingest.py`** to customize:
   - Add custom ontology for your domain's knowledge graph
   - Configure user-summary-instructions for personalized memory retrieval
   - Customize how your specific data types are ingested into Zep
   - Adjust graph structure and relationships

4. **Create custom agents in `agents.py`**:
   - Copy an existing agent class (e.g., `ChatAgent`) and rename it
   - Modify the system prompt for your domain/persona
   - Customize the Zep implementation (different retrieval methods, context formatting, etc.)
   - Your new agent will **automatically appear in the dropdown** - no other code changes needed!
   - Example: Create `CustomerSupportAgent`, `SalesAgent`, etc.

5. **Run the ingestion** and **test in the dashboard**

## Project Structure

```
zep-quickstart-dashboard/
├── README.md              # This file
├── requirements.txt       # Python dependencies
├── .env                  # Your API keys (create this)
├── .env.example          # Template
│
├── ui.py                 # Streamlit dashboard
├── agents.py             # Agent implementations
├── zep_ingest.py         # Data ingestion script
│
├── assets/               # UI assets
│   └── zep-logo.png
│
└── data/                 # Sample data
    ├── conversations.json
    └── user_data.json
```

## Learn More

- [Zep Documentation](https://help.getzep.com)
- [Retrieving Memory](https://help.getzep.com/retrieving-memory)
- [User Summary Instructions](https://help.getzep.com/user-summary-instructions)
- [Customize Context Block](https://help.getzep.com/cookbook/customize-your-context-block)

## Troubleshooting

**Import errors**
- Activate your virtual environment and run `pip install -r requirements.txt`

**API key errors**
- Check that `.env` file exists with valid `ZEP_API_KEY` and `OPENAI_API_KEY`

**No context being retrieved**
- Run `python zep_ingest.py` to populate Zep with sample data
- Make sure you're selecting a user that has data in Zep

**Ingestion fails**
- Verify `data/conversations.json` and `data/user_data.json` exist
- Check your Zep API key is valid
