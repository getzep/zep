# ElevenLabs-Zep Custom LLM Proxy

This is a proof-of-concept proxy server that sits between ElevenLabs Conversational AI and your LLM (OpenAI). It automatically injects Zep context into every request, enabling deterministic per-turn memory retrieval without tool call latency.

## Architecture

```
User speaks → ElevenLabs → This Proxy → OpenAI
                              ↓
                         Zep Retrieval
                    (inject into system prompt)
```

## Quick Start

### 1. Install Dependencies

```bash
# From the project root
source venv/bin/activate
pip install fastapi uvicorn openai zep-cloud python-dotenv
```

### 2. Set Up Test Data in Zep

```bash
python setup_test_user.py
```

This creates a test user with some conversation history and facts.

### 3. Run the Proxy Server

```bash
python proxy_server.py
```

The server will start on `http://localhost:8080`.

### 4. Test Locally (Without ElevenLabs)

In another terminal:

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

### Step 4: Pass the User ID

To identify which user's context to fetch, you need to pass the `user_id` when starting conversations. There are several ways:

**Via SDK (recommended):**
```javascript
const conversation = await Conversation.startSession({
    agentId: 'your_agent_id',
    // Pass user_id and conversation_id via customLlmExtraBody
    // This gets forwarded as "elevenlabs_extra_body" to your proxy
    customLlmExtraBody: {
        user_id: 'your-zep-user-id',
        conversation_id: 'your-zep-conversation-id'
    }
});
```

**Via dynamic variables:**
In your agent config, you can pass dynamic variables that get included in the request.

## How It Works

1. **ElevenLabs sends a request** to your proxy with the conversation messages
2. **The proxy extracts the user_id** from the request
3. **The proxy calls Zep** to get context (facts, memories) for that user
4. **The proxy injects the context** into the system prompt
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

### "No user_id provided"
Make sure you're passing the user_id when starting the ElevenLabs conversation.

### Slow responses
- Check Zep latency in the logs
- Consider caching Zep responses
- Use a faster LLM model

### Context not appearing
- Verify the user exists in Zep
- Check that facts have been extracted (may take time)
- Look at the proxy logs for the injected system prompt
