# Zep Livekit Integration Full Working Example

Voice agent that demonstrates LiveKit + Zep integration with persistent memory.

## Setup

### 1. Get API Keys

**OpenAI:** Visit [platform.openai.com](https://platform.openai.com/) and create an API key

**Zep:** Visit [cloud.getzep.com](https://cloud.getzep.com/) and create an API key

**LiveKit:** Visit [cloud.livekit.io](https://cloud.livekit.io/), create a project, and copy the connection details

### 2. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment

Create a `.env` file with your API keys:

```bash
OPENAI_API_KEY=your_openai_api_key_here
ZEP_API_KEY=your_zep_cloud_api_key_here
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key_here
LIVEKIT_API_SECRET=your_livekit_api_secret_here
```

## Usage

1. Run the agent:
```bash
python zep_voice_agent.py dev
```

2. Visit https://agents-playground.livekit.io/ and connect to agent.



## Optional: Pre-populate graph first

Run the following to add coversations from `conversations.json` to the user graph specified in `populate_memory.py`:
```bash
python populate_memory.py
```

## Optional: Try the more customizable ZepGraphAgent

1. Run the agent:
```bash
python zep_graph_voice_agent.py dev
```

2. Visit https://agents-playground.livekit.io/ and connect to agent.