"""
ElevenLabs Custom LLM Proxy with Zep Memory Integration

This proxy sits between ElevenLabs and your upstream LLM (OpenAI).
On every request, it:
1. Validates the request has a valid API key (PROXY_API_KEY)
2. Extracts the user_id and conversation_id from the request
3. Fetches relevant context from Zep (user facts + thread history)
4. Injects that context into the system prompt
5. Persists the user message to Zep for memory extraction
6. Forwards to OpenAI and streams the response back
7. Persists the assistant response to Zep

To use with ElevenLabs:
1. Set PROXY_API_KEY in your .env file (generate a random string)
2. Run this server (it will be available at http://localhost:8080)
3. Use ngrok or similar to expose it publicly
4. In ElevenLabs agent settings, set Custom LLM URL to your ngrok URL
5. In ElevenLabs, set the API key header to match your PROXY_API_KEY
"""

import os
import json
import logging
import time
import secrets
import uuid
import asyncio
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get or generate proxy API key for authentication
# This key must be provided in requests to use the proxy
PROXY_API_KEY = os.getenv("PROXY_API_KEY")
if not PROXY_API_KEY:
    # Generate a secure random key if not set
    PROXY_API_KEY = secrets.token_urlsafe(32)
    logger.warning("=" * 60)
    logger.warning("SECURITY WARNING: PROXY_API_KEY not set in .env!")
    logger.warning(f"Generated temporary key: {PROXY_API_KEY}")
    logger.warning("Add this to your .env file: PROXY_API_KEY={PROXY_API_KEY}")
    logger.warning("=" * 60)

# Initialize clients
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

app = FastAPI(title="ElevenLabs-Zep Proxy")

# Add CORS middleware to allow the React frontend to call the warm-user-cache endpoint
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def validate_api_key(request: Request) -> bool:
    """
    Validate the API key from the request.

    Checks for the API key in:
    1. Authorization header (Bearer token)
    2. X-API-Key header
    3. api-key header (OpenAI-style)
    """
    # Check Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
        if secrets.compare_digest(token, PROXY_API_KEY):
            return True

    # Check X-API-Key header
    x_api_key = request.headers.get("X-API-Key", "")
    if x_api_key and secrets.compare_digest(x_api_key, PROXY_API_KEY):
        return True

    # Check api-key header (OpenAI-style)
    api_key = request.headers.get("api-key", "")
    if api_key and secrets.compare_digest(api_key, PROXY_API_KEY):
        return True

    return False


async def ensure_zep_user_exists(user_id: str) -> bool:
    """Ensure the user exists in Zep, creating if necessary."""
    try:
        await zep_client.user.get(user_id)
        return True
    except Exception:
        try:
            await zep_client.user.add(user_id=user_id)
            logger.info(f"Created new Zep user: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to create Zep user {user_id}: {e}")
            return False


async def add_message_and_get_context(
    user_id: str,
    conversation_id: str,
    user_message: str
) -> Optional[str]:
    """
    Add the user message to Zep and get context in a single optimized call.

    PERFORMANCE OPTIMIZATION: Uses return_context=True to get Zep's full context
    block directly from add_messages(), eliminating the need for separate
    get_user_context() or graph.search() calls. The returned context block
    already includes relevant facts from the user's graph.

    Returns:
        The context block string, or None if unavailable.
    """
    start_time = time.time()

    # Ensure thread exists first
    thread_exists = await ensure_zep_thread_exists(conversation_id, user_id)
    if not thread_exists:
        logger.error(f"Could not ensure thread exists")
        return None

    # Create the user message
    message = Message(role="user", content=user_message)

    try:
        # Add message and get context in single call (performance optimization)
        # return_context=True returns Zep's full context block which includes
        # relevant facts from the user's graph - no separate search needed
        memory_response = await zep_client.thread.add_messages(
            thread_id=conversation_id,
            messages=[message],
            return_context=True
        )

        elapsed = (time.time() - start_time) * 1000
        logger.info(f"add_messages with return_context took {elapsed:.0f}ms")

        if memory_response and memory_response.context:
            logger.info(f"Got context block ({len(memory_response.context)} chars)")
            return memory_response.context

        logger.info("No context returned from add_messages")
        return None

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        logger.error(f"Error in add_messages with return_context after {elapsed:.0f}ms: {e}")
        return None


async def ensure_zep_thread_exists(thread_id: str, user_id: str) -> bool:
    """Ensure the thread exists in Zep, creating if necessary."""
    try:
        await zep_client.thread.get(thread_id=thread_id)
        return True
    except Exception:
        try:
            await zep_client.thread.create(thread_id=thread_id, user_id=user_id)
            logger.info(f"Created new Zep thread: {thread_id} for user: {user_id}")
            return True
        except Exception as e:
            # Thread might already exist (race condition), that's ok
            if "already exists" in str(e).lower() or "conflict" in str(e).lower():
                return True
            logger.error(f"Failed to create Zep thread {thread_id}: {e}")
            return False


async def persist_message_to_zep(
    user_id: str,
    conversation_id: str,
    role: str,
    content: str
) -> None:
    """
    Persist a message to Zep for memory extraction.

    This adds the message to a Zep thread, which will trigger
    automatic fact extraction and graph updates.
    """
    try:
        # Ensure thread exists first
        thread_exists = await ensure_zep_thread_exists(conversation_id, user_id)
        if not thread_exists:
            logger.error(f"Could not ensure thread exists, skipping message persistence")
            return

        # Create message with the correct format for Zep thread API
        # role should be "user" or "assistant"
        message = Message(
            role=role,  # "user" or "assistant"
            content=content
        )

        # Add message to thread
        await zep_client.thread.add_messages(
            thread_id=conversation_id,
            messages=[message]
        )
        logger.info(f"Persisted {role} message to Zep thread {conversation_id}")

    except Exception as e:
        logger.error(f"Error persisting message to Zep thread: {e}")
        # Log more details for debugging
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")


def inject_context_into_messages(messages: list, zep_context: str) -> list:
    """
    Inject Zep context into the system prompt.

    If there's an existing system message, append the context.
    If not, create a new system message with the context.
    """
    if not zep_context:
        return messages

    context_block = f"""

<zep_user_context>
The following context about this user was retrieved from your memory system (Zep).
Use this information to personalize your responses and maintain continuity across conversations.

{zep_context}
</zep_user_context>
"""

    # Make a copy to avoid modifying the original
    modified_messages = messages.copy()

    if modified_messages and modified_messages[0].get("role") == "system":
        # Append to existing system message
        modified_messages[0] = {
            **modified_messages[0],
            "content": modified_messages[0]["content"] + context_block
        }
    else:
        # Insert a new system message at the beginning
        modified_messages.insert(0, {
            "role": "system",
            "content": f"You are a helpful assistant.{context_block}"
        })

    return modified_messages


async def stream_openai_response(
    request_body: dict,
    messages: list,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """
    Stream the response from OpenAI back to ElevenLabs.

    ElevenLabs expects SSE format matching OpenAI's streaming response.
    Also collects the full response to persist to Zep.
    """
    full_response_content = []

    try:
        # Prepare the request for OpenAI
        openai_request = {
            "model": request_body.get("model", "gpt-4o-mini"),
            "messages": messages,
            "stream": True,
        }

        # Add optional parameters if present
        if "temperature" in request_body:
            openai_request["temperature"] = request_body["temperature"]
        if "max_tokens" in request_body:
            openai_request["max_tokens"] = request_body["max_tokens"]

        # Handle tools if ElevenLabs sends them
        if "tools" in request_body:
            openai_request["tools"] = request_body["tools"]

        logger.info(f"Sending request to OpenAI with model: {openai_request['model']}")

        # Stream from OpenAI
        response = await openai_client.chat.completions.create(**openai_request)

        async for chunk in response:
            # Convert chunk to SSE format
            chunk_dict = chunk.model_dump()
            yield f"data: {json.dumps(chunk_dict)}\n\n"

            # Collect content for Zep persistence
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                full_response_content.append(chunk.choices[0].delta.content)

        # Send the [DONE] marker
        yield "data: [DONE]\n\n"

        # Persist the assistant response to Zep (fire and forget)
        if user_id and conversation_id and full_response_content:
            full_text = "".join(full_response_content)
            asyncio.create_task(
                persist_message_to_zep(user_id, conversation_id, "assistant", full_text)
            )

    except Exception as e:
        logger.error(f"Error streaming from OpenAI: {e}")
        # Send an error message in a format ElevenLabs can handle
        error_chunk = {
            "id": "error",
            "object": "chat.completion.chunk",
            "choices": [{
                "index": 0,
                "delta": {"content": f"Error: {str(e)}"},
                "finish_reason": "stop"
            }]
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Main endpoint that ElevenLabs will call.

    This mimics the OpenAI chat completions endpoint but:
    1. Fetches context from Zep (user facts + session history)
    2. Injects that context into the system prompt
    3. Persists messages to Zep for memory extraction
    4. Forwards to OpenAI and streams the response back

    AUTHENTICATION REQUIRED: Include your PROXY_API_KEY in one of:
    - Authorization: Bearer <key>
    - X-API-Key: <key>
    - api-key: <key>

    Pass user_id and conversation_id via elevenlabs_extra_body from the SDK.
    """
    # Validate API key before processing
    if not validate_api_key(request):
        logger.warning(f"Unauthorized request from {request.client.host}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include PROXY_API_KEY in Authorization header."
        )

    try:
        # Parse the incoming request
        body = await request.json()

        logger.info("=" * 50)
        logger.info("Received request from ElevenLabs")
        logger.info(f"Model requested: {body.get('model', 'not specified')}")
        logger.info(f"Number of messages: {len(body.get('messages', []))}")

        # Log all top-level keys in the request body to see what ElevenLabs sends
        logger.info(f"Request body keys: {list(body.keys())}")

        # Log the actual messages for debugging
        for i, msg in enumerate(body.get("messages", [])):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Truncate long messages but show user messages in full (they're usually short)
            if role == "user":
                logger.info(f"Message {i} [{role}]: {content}")
            else:
                logger.info(f"Message {i} [{role}]: {content[:200]}..." if len(str(content)) > 200 else f"Message {i} [{role}]: {content}")

        # ============================================================
        # DEBUG: Log full request structure to find conversation_id
        # ============================================================
        logger.info("--- FULL REQUEST BODY (excluding messages) ---")
        for key, value in body.items():
            if key == "messages":
                continue  # Skip messages, already logged above
            elif isinstance(value, dict):
                logger.info(f"  {key}: {json.dumps(value, indent=4)}")
            elif isinstance(value, list):
                logger.info(f"  {key}: {value}")
            else:
                logger.info(f"  {key}: {value}")
        logger.info("--- END FULL REQUEST BODY ---")

        # Extract from elevenlabs_extra_body (this is where customLlmExtraBody ends up)
        extra_body = body.get("elevenlabs_extra_body", {})
        user_id = extra_body.get("user_id")
        conversation_id = extra_body.get("conversation_id")

        # Also check if conversation_id exists elsewhere in the request
        logger.info("--- SEARCHING FOR CONVERSATION_ID ---")
        logger.info(f"  In elevenlabs_extra_body: {conversation_id}")

        possible_keys = ["conversation_id", "session_id", "call_id", "id", "conv_id"]
        for key in possible_keys:
            if key in body:
                logger.info(f"  Found body['{key}']: {body[key]}")
                if not conversation_id:
                    conversation_id = body[key]

        # Validate required fields
        if not user_id:
            logger.error("ERROR: No user_id found!")
            raise HTTPException(status_code=400, detail="user_id is required in elevenlabs_extra_body")

        if not conversation_id:
            logger.error("ERROR: No conversation_id found anywhere in the request!")
            raise HTTPException(status_code=400, detail="conversation_id is required")

        logger.info(f"✓ Using User ID: {user_id}")
        logger.info(f"✓ Using Conversation ID: {conversation_id}")

        # Ensure user exists in Zep
        await ensure_zep_user_exists(user_id)

        # Get original messages
        messages = body.get("messages", [])

        # Find the latest user message
        user_message = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # PERFORMANCE OPTIMIZATION: Use add_message_and_get_context which
        # adds the user message to Zep AND returns the full context block in
        # a single call via return_context=True. This eliminates the need for
        # separate get_user_context() or graph.search() calls.
        if user_message:
            zep_context = await add_message_and_get_context(
                user_id, conversation_id, user_message
            )
        else:
            # No user message - this shouldn't normally happen
            zep_context = None

        messages = inject_context_into_messages(messages, zep_context)
        if zep_context:
            logger.info("Injected Zep context into messages")
        else:
            logger.info("No Zep context found to inject")

        # Log the modified system prompt (truncated for readability)
        if messages and messages[0].get("role") == "system":
            system_content = messages[0]["content"]
            logger.info(f"System prompt (first 500 chars): {system_content[:500]}...")

        # Stream the response back (also persists assistant response to Zep)
        return StreamingResponse(
            stream_openai_response(body, messages, user_id, conversation_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    except Exception as e:
        logger.error(f"Error in chat_completions: {e}")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "elevenlabs-zep-proxy"}


@app.post("/warm-user-cache")
async def warm_user_cache(request: Request):
    """
    Warm the Zep cache for a user before they start a conversation.

    PERFORMANCE OPTIMIZATION: Call this endpoint when a user arrives on your page,
    before they start speaking. This moves the user's data into Zep's "hot" cache,
    making subsequent context retrieval faster.

    Zep has a multi-tier retrieval architecture. After several hours of inactivity,
    user data moves to a lower (slower) tier. This endpoint hints to Zep that a
    retrieval is coming soon, allowing it to pre-warm the cache.

    Good times to call this:
    - When user logs in
    - When user navigates to the voice chat page
    - When user clicks "Start Conversation" (before actually starting)
    """
    try:
        body = await request.json()
        user_id = body.get("user_id")

        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")

        logger.info(f"Warming cache for user: {user_id}")

        # Ensure user exists first
        await ensure_zep_user_exists(user_id)

        # Warm the user's cache
        # Note: This may fail with 404 if the user has no graph data yet (new user)
        # That's fine - there's nothing to warm for new users
        start_time = time.time()
        try:
            await zep_client.user.warm(user_id=user_id)
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"Warmed cache for user {user_id} in {elapsed:.0f}ms")
            return {
                "status": "success",
                "user_id": user_id,
                "message": "User cache warmed successfully"
            }
        except Exception as warm_error:
            # Check if this is a "no graph data" error - that's expected for new users
            error_str = str(warm_error).lower()
            if "not found" in error_str or "graph data" in error_str:
                logger.info(f"No graph data to warm for user {user_id} (new user)")
                return {
                    "status": "success",
                    "user_id": user_id,
                    "message": "User is new - no cache to warm yet"
                }
            # Re-raise other errors
            raise

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error warming cache for user: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint with usage info."""
    return {
        "service": "ElevenLabs-Zep Proxy",
        "description": "Custom LLM proxy that injects Zep context into every request",
        "endpoints": {
            "POST /v1/chat/completions": "Main endpoint for ElevenLabs",
            "POST /warm-user-cache": "Pre-warm Zep cache for a user (call when user arrives on page)",
            "GET /health": "Health check",
        },
        "usage": "Set this URL as your Custom LLM endpoint in ElevenLabs agent settings",
        "performance_tips": [
            "Call /warm-user-cache when a user arrives on your page, before they start speaking",
            "This moves user data into Zep's hot cache for faster retrieval"
        ]
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))
    logger.info(f"Starting ElevenLabs-Zep Proxy on port {port}")
    logger.info("Endpoints:")
    logger.info(f"  - POST http://localhost:{port}/v1/chat/completions")
    logger.info(f"  - GET  http://localhost:{port}/health")

    uvicorn.run(app, host="0.0.0.0", port=port)
