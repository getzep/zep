# ElevenLabs-Zep Custom LLM Proxy

This is a proof-of-concept proxy server that sits between ElevenLabs Conversational AI and your LLM (OpenAI). It automatically injects Zep context into every request, enabling deterministic per-turn memory retrieval without tool call latency.

## Architecture

```
User speaks → ElevenLabs → This Proxy → OpenAI
                              ↓
                         Zep Retrieval
                    (add as user-message data)
```

## Quick Start

### 1. Install Dependencies

```bash
# From llm-proxy/
pip install -r requirements.txt
```

### 2. Create Test Data in Zep (Optional)

```bash
python setup_test_user.py
```

This creates a test user with some conversation history and facts.

`test_proxy_locally.py` does not require this step. It creates a temporary user and thread, tests two chat turns, and deletes the resources when it finishes.

### 3. Run the Proxy Server

```bash
python proxy_server.py
```

The server will start on `http://localhost:8080`.

### 4. Test Locally (Without ElevenLabs)

In another terminal. This test creates and removes its own Zep user and thread:

```bash
python test_proxy_locally.py
```

This simulates what ElevenLabs would send and shows the injected context.

## Connecting to ElevenLabs

### Step 1: Expose Your Proxy Publicly

ElevenLabs needs to reach your proxy over the internet. Options:

**Option A: ngrok (easiest for testing)**
```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8080
```

You'll get a URL like `https://abc123.ngrok.io`

**Option B: Deploy to a cloud service**
- Railway, Render, Fly.io, etc.

### Step 2: Get Your ElevenLabs API Key

1. Go to https://elevenlabs.io
2. Sign in or create an account
3. Go to your Profile (bottom left) → API Keys
4. Copy your API key

### Step 3: Create/Configure an Agent

1. Go to https://elevenlabs.io/app/conversational-ai
2. Create a new agent or select an existing one
3. In the agent settings, go to **Model** section
4. Select **Custom LLM**
5. Enter your proxy URL: `https://your-ngrok-url.ngrok.io/v1/chat/completions`
6. Save the agent

### Step 4: Create Zep UUIDs for Each Session

The React app creates a Zep user through `POST /zep/users` and stores the returned `user_uuid` in `localStorage` as `zep_user_uuid`. It creates a Zep thread through `POST /zep/threads` when a session starts.

**Via SDK (recommended):**
```javascript
const conversation = await Conversation.startSession({
    agentId: 'your_agent_id',
    // These existing keys carry Zep UUIDs and are forwarded to the proxy.
    customLlmExtraBody: {
        user_id: 'your-zep-user-uuid',
        conversation_id: 'your-zep-thread-uuid'
    }
});
```

The proxy provides `POST /zep/users` and `POST /zep/threads` to create the UUIDs. The `/warm-user-cache` endpoint also receives the Zep user UUID as `user_id`.

## How It Works

1. **ElevenLabs sends a request** to your proxy with the conversation messages
2. **The proxy extracts the Zep user and thread UUIDs** from the request
3. **The proxy calls Zep** to get context (facts, memories) for that user
4. **The proxy adds the context** to a user message as data
5. **The proxy forwards to OpenAI** and streams the response back
6. **ElevenLabs receives the response** with the context already incorporated

## Files

- `proxy_server.py` - Main proxy server
- `setup_test_user.py` - Create test data in Zep
- `test_proxy_locally.py` - Test without ElevenLabs

## Customization

### Change the Upstream LLM

Edit `proxy_server.py` to use a different provider:
- Anthropic Claude
- Google Gemini
- Local models

### Modify Context Injection

The `inject_context_into_messages()` function controls how context is added. You can:
- Add context as a separate user message
- Use different formatting
- Filter which facts to include

### Adjust Zep Retrieval

The `get_zep_context()` function controls what's fetched from Zep. You can:
- Use graph search instead of memory.get()
- Filter facts by relevance
- Include/exclude certain types of data

## Troubleshooting

### "user_id is required"
Create a Zep user and pass its returned UUID as `user_id` when starting the ElevenLabs conversation.

### Slow responses
- Check Zep latency in the logs
- Consider caching Zep responses
- Use a faster LLM model

### Context not appearing
- Verify the user exists in Zep
- Check that facts have been extracted (may take time)
- Look at the proxy logs for the context added to the user message
