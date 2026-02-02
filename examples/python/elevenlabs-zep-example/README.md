# ElevenLabs + Zep Integration Example

This example demonstrates how to integrate Zep's context store with ElevenLabs Conversational AI agents.

## How It Works

ElevenLabs agents support a **Custom LLM** option that lets you route LLM requests through your own server instead of directly to OpenAI/Anthropic. This example uses that feature to insert a proxy server that:

1. Receives the transcribed user message from ElevenLabs
2. Retrieves relevant context from Zep for the user
3. Injects that context into the prompt
4. Forwards to OpenAI for response generation
5. Persists both user and assistant messages to Zep

## Architecture

```
                  user audio                   user text                 msg + context
┌─────────────┐                ┌─────────────┐                ┌─────────────┐                ┌─────────────┐
│             │     ─────→     │             │     ─────→     │             │     ─────→     │             │
│  React App  │     ←─────     │  ElevenLabs │     ←─────     │  LLM Proxy  │     ←─────     │   OpenAI    │
│             │                │             │                │             │                │             │
└─────────────┘                └─────────────┘                └─────────────┘                └─────────────┘
                 agent audio                    response                     response
                                                                   │    ↑
                                                            msgs   ↓    │   context
                                                               ┌─────────────┐
                                                               │             │
                                                               │     Zep     │
                                                               │             │
                                                               └─────────────┘
```

## Why This Approach?

The alternative would be exposing Zep as a **tool** within the ElevenLabs agent platform. However, tool-based integrations have two drawbacks:

1. **Latency**: Every time the LLM decides whether to call a tool, it adds latency. For voice agents where responsiveness is critical, this delay is noticeable.

2. **Unreliability**: The LLM may not call the tool every time. It decides whether retrieving context is relevant for each message, and it won't always make the right call.

The proxy approach solves both problems - the LLM never has to decide whether to use Zep. Context retrieval and persistence happen transparently on every request.

## Prerequisites

Before starting, you'll need:

| Service | What You Need | Where to Get It |
|---------|---------------|-----------------|
| **ElevenLabs** | Account + Agent ID (no API key needed) | [elevenlabs.io/app/conversational-ai](https://elevenlabs.io/app/conversational-ai) |
| **Zep** | API Key (context engineering platform) | [app.getzep.com](https://app.getzep.com) (Settings > API Keys) |
| **OpenAI** | API Key | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| **ngrok** | Account (free tier works) | [ngrok.com/download](https://ngrok.com/download) |

You'll also need:
- **Node.js 18+**: [nodejs.org](https://nodejs.org/)
- **Python 3.9+**: [python.org](https://www.python.org/downloads/)

## Setup Instructions

### Step 1: Configure the LLM Proxy

1. Navigate to the proxy directory:
   ```bash
   cd llm-proxy
   ```

2. Create a `.env` file from the example:
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and fill in your API keys:
   - `ZEP_API_KEY`: From [Zep Dashboard](https://app.getzep.com) > Settings > API Keys
   - `OPENAI_API_KEY`: From [OpenAI Platform](https://platform.openai.com/api-keys)
   - `PROXY_API_KEY`: A secret password you make up (e.g., `my-secret-proxy-key-12345`). This prevents unauthorized access to your proxy. You'll enter this same value in the ElevenLabs dashboard in Step 3.

3. Install Python dependencies:
   ```bash
   pip install fastapi uvicorn openai zep-cloud python-dotenv
   ```

4. Start the proxy server:
   ```bash
   python proxy_server.py
   ```

   You should see:
   ```
   INFO:     Starting ElevenLabs-Zep Proxy on port 8080
   INFO:     Endpoints:
   INFO:       - POST http://localhost:8080/v1/chat/completions
   INFO:       - GET  http://localhost:8080/health
   ```

### Step 2: Expose the Proxy with ngrok

In a **new terminal**:

```bash
ngrok http 8080
```

Copy the HTTPS URL (e.g., `https://abc123.ngrok-free.app`). You'll need this for ElevenLabs.

> **Note:** ngrok free tier generates a new URL each time you restart. Consider a paid plan for a stable URL, or use a cloud deployment for production.

### Step 3: Configure Your ElevenLabs Agent

1. Go to [ElevenLabs Conversational AI](https://elevenlabs.io/app/conversational-ai)

2. Create a new agent or select an existing one

3. **Go to the Agent tab** and find the **LLM** section

4. **Select "Custom LLM"** as the LLM provider (instead of OpenAI, Anthropic, etc.)

5. **Configure the Custom LLM settings**:
   - **Server URL**: Your ngrok URL + `/v1/chat/completions`
     - Example: `https://abc123.ngrok-free.app/v1/chat/completions`
   - **Model ID**: `gpt-4o-mini` (or whichever OpenAI model you want the proxy to use)

6. **Add authentication header**:
   - Click "Add Secret Header" or similar
   - **Header Name**: `Authorization`
   - **Header Value**: `Bearer my-secret-proxy-key-12345`
     - Use the exact same `PROXY_API_KEY` value from your `.env` file
     - Make sure to include `Bearer ` before the key

7. **Enable Custom LLM Extra Body** (CRITICAL):
   - Go to the **Security** tab in the agent settings
   - Scroll down to the **Overrides** section
   - Find **"Custom LLM extra body"**
   - **Toggle it ON**
   - Save the agent

   > This setting allows the React app to pass `user_id` and `conversation_id` to your proxy. Without it, the integration won't work.

8. **Copy your Agent ID** from the URL:
   - The URL looks like: `elevenlabs.io/app/conversational-ai/agents/AGENT_ID_HERE`
   - You'll need this for the React app

### Step 4: Set Up the React App

1. Navigate to the React app directory:
   ```bash
   cd react-app
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Create a `.env` file with your Agent ID (from Step 3.8):
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and set your Agent ID:
   ```
   VITE_ELEVENLABS_AGENT_ID=your_agent_id_here
   ```

4. Start the development server:
   ```bash
   npm run dev
   ```

   The app will be available at `http://localhost:5173`

### Step 5: Test the Integration

1. Open `http://localhost:5173` in your browser

2. Click **"Start Conversation"**

3. Allow microphone access when prompted

4. Speak to the agent!

**Verify it's working by checking:**

- **React app**: Status shows "connected", User ID and Conversation ID are displayed
- **Proxy logs**: You should see messages like:
  ```
  INFO: User ID: user-abc12345
  INFO: Conversation ID: conv-xyz789...
  INFO: Fetching Zep context for user: user-abc12345
  INFO: Persisted user message to Zep thread...
  ```
- **Zep Dashboard**: The user and thread should appear at [app.getzep.com](https://app.getzep.com)

## Components

### 1. React App (`react-app/`)
- Vite + React application
- Uses `@11labs/react` SDK for voice conversations
- Manages user identity and conversation sessions
- Passes identifiers via `customLlmExtraBody`
- Includes cache warming for faster first responses

### 2. LLM Proxy (`llm-proxy/`)
- FastAPI Python server
- OpenAI-compatible `/v1/chat/completions` endpoint
- Integrates with Zep for context retrieval and conversation persistence
- Streams responses back to ElevenLabs

## Data Flow Details

### User Identification

The React app generates and persists a `user_id` in localStorage:
- Format: `user-{8-char-uuid}` (e.g., `user-a1b2c3d4`)
- Persists across browser sessions
- Can be reset via UI button for testing

### Conversation Tracking

Each voice session gets a unique `conversation_id`:
- Format: `conv-{12-char-uuid}` (e.g., `conv-x1y2z3a4b5c6`)
- Pre-generated in React before starting the ElevenLabs session
- Maps to a Zep "thread" for conversation history

### Why Pre-Generate conversation_id?

ElevenLabs generates its own conversation_id, but it's only available **after** `startSession()` returns. However, `customLlmExtraBody` is sent **when** the session starts. This creates a chicken-and-egg problem:

- We need to send the conversation_id in customLlmExtraBody
- But ElevenLabs' conversation_id isn't available until after

**Solution:** Generate our own conversation_id before calling `startSession()` and use that for Zep tracking.

## Troubleshooting

### "No user_id in request" error
Make sure you enabled **"Custom LLM extra body"** in the agent's Security settings (Step 3.7).

### Proxy authentication fails (401)
- Verify the `PROXY_API_KEY` in your proxy's `.env` matches the header value in ElevenLabs
- Check the header format is exactly `Authorization: Bearer YOUR_KEY`

### ngrok URL changes
ngrok free tier generates a new URL each restart. Update the Custom LLM URL in ElevenLabs dashboard and restart the conversation.

### Microphone not working
- Check browser permissions (click the lock icon in the address bar)
- Ensure you're using HTTPS or localhost

### Zep context not appearing
- Check that your `ZEP_API_KEY` is correct
- Verify the user exists in [Zep Dashboard](https://app.getzep.com)
- Check proxy logs for Zep-related errors

## Limitations

- **No native conversation_id forwarding**: ElevenLabs doesn't pass their conversation_id to custom LLM endpoints, hence the pre-generation approach
- **Requires ngrok for development**: ElevenLabs needs a publicly accessible URL for the custom LLM
- **Latency**: The proxy adds a round-trip to Zep, though this is typically minimal (~50-100ms)

## Performance Optimization

The React app includes a "cache warming" feature that pre-fetches the user's context from Zep when the page loads. This moves the user's data into Zep's hot cache before they start speaking, reducing latency on the first message.

## Production Considerations

This example is designed for learning and development. For production deployments:

- **Cache warming endpoint**: The `/warm-user-cache` endpoint is currently unauthenticated to keep the demo simple. In production, this should be called from your authenticated backend rather than directly from the React frontend. This prevents unauthorized users from making arbitrary warm requests to your proxy.

- **CORS settings**: The proxy currently allows all origins (`allow_origins=["*"]`). Restrict this to your frontend domain in production.

- **Stable URL**: Replace ngrok with a proper cloud deployment (Railway, Render, AWS, etc.) for a stable URL that doesn't change.
