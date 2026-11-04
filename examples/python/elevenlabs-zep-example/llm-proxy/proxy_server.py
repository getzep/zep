"""
ElevenLabs Custom LLM Proxy with Zep Memory Integration

This proxy sits between ElevenLabs and your upstream LLM (OpenAI).
On every request, it:
1. Validates the request has a valid API key (PROXY_API_KEY)
2. Extracts the user UUID and the conversation ID from the request
3. Fetches relevant context from Zep (user facts + thread history)
4. Injects that context into the instructions
5. Persists the user message to Zep for memory extraction
6. Forwards to the OpenAI Responses API with low reasoning and streams the response back
7. Persists the assistant response to Zep

To use with ElevenLabs:
1. Set PROXY_API_KEY in your .env file (generate a random string)
2. Run this server (it will be available at http://localhost:8080)
3. Use ngrok or similar to expose it publicly
4. In ElevenLabs agent settings, set Custom LLM URL to your ngrok URL + /v1/responses
5. In ElevenLabs, set the API key header to match your PROXY_API_KEY
"""

import asyncio
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from zep_cloud import AddMessage
from zep_cloud.client import AsyncZep

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get proxy API key for authentication
# This key must be provided in requests to use the proxy
PROXY_API_KEY = os.getenv("PROXY_API_KEY")
if not PROXY_API_KEY:
    raise ValueError(
        "PROXY_API_KEY environment variable is required. "
        "Set it in your .env file (e.g., PROXY_API_KEY=your-secret-key-here)"
    )

# Initialize clients
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
zep_client = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

app = FastAPI(title="ElevenLabs-Zep Proxy")

# The model that the proxy uses when ElevenLabs does not send a Model ID
DEFAULT_MODEL = "gpt-6-luna"

# The Responses API fields that the proxy forwards from ElevenLabs to OpenAI
FORWARDED_RESPONSES_FIELDS = (
    "instructions",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "max_output_tokens",
    "text",
)

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


# v4 addresses a thread by its server-generated UUID. The proxy keeps the
# UUID of the thread of each ElevenLabs conversation in memory. A production
# application keeps this map in its own database.
CONVERSATION_THREAD_UUIDS: dict = {}


async def ensure_zep_user_exists(user_uuid: str) -> bool:
    """Make sure that the user exists in Zep."""
    try:
        await zep_client.user.get(user_uuid)
        return True
    except Exception:
        return False


async def add_message_and_get_context(
    user_uuid: str,
    conversation_id: str,
    user_message: str
) -> Optional[str]:
    """
    Add the user message to Zep and get context in a single optimized call.

    PERFORMANCE OPTIMIZATION: Uses return_context=True to get Zep's full context
    block directly from add_messages(), eliminating the need for separate
    get_context() or graph.search_edges() calls. The returned context block
    already includes relevant facts from the user's graph.

    Returns:
        The context block string, or None if unavailable.
    """
    # Get the UUID of the thread of this conversation
    thread_uuid = await get_or_create_thread_uuid(conversation_id, user_uuid)
    if not thread_uuid:
        return None

    # Create the user message
    message = AddMessage(role="user", content=user_message)

    try:
        # Add message and get context in single call (performance optimization)
        # return_context=True returns Zep's full context block which includes
        # relevant facts from the user's graph - no separate search needed
        memory_response = await zep_client.thread.add_messages(
            thread_uuid,
            messages=[message],
            return_context=True
        )

        if memory_response and memory_response.context:
            return memory_response.context

        return None

    except Exception:
        return None


async def get_or_create_thread_uuid(
    conversation_id: str, user_uuid: str
) -> Optional[str]:
    """Get the UUID of the thread of a conversation, or create the thread.

    v4 creates a thread with a server-generated UUID. The proxy stores that
    UUID against the ElevenLabs conversation ID.
    """
    thread_uuid = CONVERSATION_THREAD_UUIDS.get(conversation_id)
    if thread_uuid:
        return thread_uuid

    try:
        thread = await zep_client.thread.create(user_uuid=user_uuid)
    except Exception:
        return None

    CONVERSATION_THREAD_UUIDS[conversation_id] = thread.uuid_
    return thread.uuid_


async def persist_message_to_zep(
    user_uuid: str,
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
        # Get the UUID of the thread of this conversation
        thread_uuid = await get_or_create_thread_uuid(conversation_id, user_uuid)
        if not thread_uuid:
            return

        # Create message with the correct format for Zep thread API
        message = AddMessage(role=role, content=content)

        # Add message to thread
        await zep_client.thread.add_messages(
            thread_uuid,
            messages=[message]
        )

    except Exception:
        # Silently fail - don't interrupt the response stream
        pass


def zep_context_block(zep_context: str) -> str:
    """Format the Zep context for the instructions of the model."""
    return f"""

<zep_user_context>
The following context about this user was retrieved from your memory system (Zep).
Use this information to personalize your responses and maintain continuity across conversations.

{zep_context}
</zep_user_context>
"""


def message_text(content) -> str:
    """Get the text of a Responses API message content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text")
        )
    return ""


def new_user_turn(input_items) -> Optional[str]:
    """Get a user turn only when it is the final input item.

    A tool continuation repeats earlier turns. Those turns are already in
    Zep and must not be added again.
    """
    if isinstance(input_items, str):
        return input_items or None
    if input_items:
        item = input_items[-1]
        if isinstance(item, dict) and item.get("role") == "user":
            return message_text(item.get("content")) or None
    return None


# The Responses API returns reasoning items before function calls. The model
# needs those reasoning items again when the next request continues the tool
# loop. ElevenLabs keeps the conversation history, so the proxy keeps the
# reasoning items of each function call in memory and adds them back when
# ElevenLabs does not send them. The map keeps at most
# REASONING_CACHE_MAX_CALLS calls, and each entry expires after
# REASONING_CACHE_TTL_SECONDS. A production application keeps this map in
# its own store.
REASONING_CACHE_MAX_CALLS = 1000
REASONING_CACHE_TTL_SECONDS = 3600
REASONING_ITEMS_BY_CALL_ID: OrderedDict = OrderedDict()


def prune_reasoning_items(now: float) -> None:
    """Remove expired entries and the oldest entries above the size limit."""
    while REASONING_ITEMS_BY_CALL_ID:
        call_id, (stored_at, _) = next(iter(REASONING_ITEMS_BY_CALL_ID.items()))
        expired = now - stored_at > REASONING_CACHE_TTL_SECONDS
        if not expired and len(REASONING_ITEMS_BY_CALL_ID) <= REASONING_CACHE_MAX_CALLS:
            break
        del REASONING_ITEMS_BY_CALL_ID[call_id]


def remember_reasoning_items(output_items: list) -> None:
    """Keep the reasoning items of a response that requests function calls."""
    reasoning_items = [item for item in output_items if item.get("type") == "reasoning"]
    if not reasoning_items:
        return
    now = time.monotonic()
    for item in output_items:
        if item.get("type") == "function_call" and item.get("call_id"):
            REASONING_ITEMS_BY_CALL_ID.pop(item["call_id"], None)
            REASONING_ITEMS_BY_CALL_ID[item["call_id"]] = (now, reasoning_items)
    prune_reasoning_items(now)


def kept_reasoning_items(call_id) -> list:
    """Get the reasoning items of a function call that did not expire."""
    prune_reasoning_items(time.monotonic())
    entry = REASONING_ITEMS_BY_CALL_ID.get(call_id)
    return entry[1] if entry else []


def restore_reasoning_items(input_items):
    """Put the kept reasoning items before the function calls that need them."""
    if not isinstance(input_items, list):
        return input_items
    present_ids = {
        item.get("id")
        for item in input_items
        if isinstance(item, dict) and item.get("type") == "reasoning"
    }
    restored = []
    for item in input_items:
        if isinstance(item, dict) and item.get("type") == "function_call":
            previous_type = restored[-1].get("type") if restored and isinstance(restored[-1], dict) else None
            reasoning_items = kept_reasoning_items(item.get("call_id"))
            if previous_type not in ("reasoning", "function_call"):
                for reasoning_item in reasoning_items:
                    if reasoning_item.get("id") not in present_ids:
                        restored.append(reasoning_item)
                        present_ids.add(reasoning_item.get("id"))
        restored.append(item)
    return restored


def build_openai_request(body: dict, zep_context: Optional[str]) -> dict:
    """Make the OpenAI Responses API request from the ElevenLabs request.

    GPT-6 Luna supports function tools with low reasoning on the Responses
    API. The proxy always sets low reasoning and keeps a reasoning summary
    setting from ElevenLabs. The proxy does not forward sampling parameters
    such as temperature, because GPT-6 Luna does not accept them.

    The proxy requires the full input history for each request. It rejects
    previous_response_id because responses are not stored on the OpenAI
    server. When Zep returns context, the proxy appends the
    context to the instructions of the caller. OpenAI does not carry the
    instructions of a previous response over to the next response, so each
    request has its own instructions.
    """
    openai_request = {
        field: body[field] for field in FORWARDED_RESPONSES_FIELDS if field in body
    }
    openai_request["model"] = body.get("model") or DEFAULT_MODEL
    openai_request["input"] = restore_reasoning_items(body.get("input", []))
    openai_request["reasoning"] = {**(body.get("reasoning") or {}), "effort": "low"}
    openai_request["store"] = False
    openai_request["include"] = ["reasoning.encrypted_content"]
    if zep_context:
        instructions = body.get("instructions") or ""
        openai_request["instructions"] = (instructions + zep_context_block(zep_context)).lstrip()
    openai_request["stream"] = True
    return openai_request


def sse_event(event_type: str, data: dict) -> str:
    """Format one Responses API server-sent event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def stream_openai_response(
    openai_request: dict,
    user_uuid: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """
    Stream the OpenAI Responses API events back to ElevenLabs.

    ElevenLabs reads the Responses API events, which include the text deltas
    and the function calls. The proxy also collects the full text to persist
    to Zep.
    """
    full_response_content = []

    try:
        response = await openai_client.responses.create(**openai_request)

        async for event in response:
            yield sse_event(event.type, event.model_dump(mode="json"))

            if event.type == "response.output_text.delta":
                full_response_content.append(event.delta)
            elif event.type == "response.completed":
                remember_reasoning_items(
                    [item.model_dump(mode="json", exclude_none=True) for item in event.response.output]
                )

        # Send the [DONE] marker
        yield "data: [DONE]\n\n"

        # Persist the assistant response to Zep (fire and forget)
        if user_uuid and conversation_id and full_response_content:
            full_text = "".join(full_response_content)
            asyncio.create_task(
                persist_message_to_zep(user_uuid, conversation_id, "assistant", full_text)
            )

    except Exception as e:
        logger.error(f"Error streaming from OpenAI: {e}")
        # Send an error event in the format that ElevenLabs reads
        yield sse_event(
            "error",
            {"type": "error", "error": {"message": "An error occurred processing your request."}},
        )
        yield "data: [DONE]\n\n"


@app.post("/v1/responses")
async def responses(request: Request):
    """
    Main endpoint that ElevenLabs will call.

    This mimics the OpenAI Responses API endpoint but:
    1. Fetches context from Zep (user facts + session history)
    2. Injects that context into the instructions
    3. Persists messages to Zep for memory extraction
    4. Forwards to OpenAI with low reasoning and streams the response back

    AUTHENTICATION REQUIRED: Include your PROXY_API_KEY in one of:
    - Authorization: Bearer <key>
    - X-API-Key: <key>
    - api-key: <key>

    Pass user_uuid and conversation_id via elevenlabs_extra_body from the SDK.
    """
    # Validate API key before processing
    if not validate_api_key(request):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include PROXY_API_KEY in Authorization header."
        )

    try:
        # Parse the incoming request
        body = await request.json()

        if body.get("previous_response_id"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "previous_response_id is not supported because the proxy uses store=False. "
                    "Send the full conversation history in input, including function calls "
                    "and function-call outputs."
                ),
            )

        # Extract from elevenlabs_extra_body (this is where customLlmExtraBody ends up)
        extra_body = body.get("elevenlabs_extra_body") or {}
        user_uuid = extra_body.get("user_uuid")
        conversation_id = extra_body.get("conversation_id")

        # Also check if conversation_id exists elsewhere in the request
        possible_keys = ["conversation_id", "session_id", "call_id", "id", "conv_id"]
        for key in possible_keys:
            if key in body and not conversation_id:
                conversation_id = body[key]

        # Validate required fields
        if not user_uuid:
            raise HTTPException(status_code=400, detail="user_uuid is required in elevenlabs_extra_body")

        if not conversation_id:
            raise HTTPException(status_code=400, detail="conversation_id is required")

        # Make sure that the user exists in Zep
        await ensure_zep_user_exists(user_uuid)

        # Find the latest user message
        user_message = new_user_turn(body.get("input", []))

        # PERFORMANCE OPTIMIZATION: Use add_message_and_get_context which
        # adds the user message to Zep AND returns the full context block in
        # a single call via return_context=True. This eliminates the need for
        # separate get_context() or graph.search_edges() calls.
        if user_message:
            zep_context = await add_message_and_get_context(
                user_uuid, conversation_id, user_message
            )
        else:
            # Tool continuations do not add another user turn.
            zep_context = None

        openai_request = build_openai_request(body, zep_context)

        # Stream the response back (also persists assistant response to Zep)
        return StreamingResponse(
            stream_openai_response(openai_request, user_uuid, conversation_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in responses: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "elevenlabs-zep-proxy"}


@app.post("/create-user")
async def create_user(request: Request):
    """Create a Zep user and give its UUID to the application.

    v4 gives every user a server-generated UUID. The browser cannot create a
    user, because the browser has no Zep API key. The application stores the
    UUID and sends it with each conversation.
    """
    try:
        body = await request.json()
        user = await zep_client.user.create(
            first_name=body.get("first_name"),
            last_name=body.get("last_name"),
            email=body.get("email"),
        )
        return {"status": "success", "user_uuid": user.uuid_}
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error while creating the user")


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
        user_uuid = body.get("user_uuid")

        if not user_uuid:
            raise HTTPException(status_code=400, detail="user_uuid is required")

        # Get the user, which gives the UUID of the graph of the user
        user = await zep_client.user.get(user_uuid)

        # Warm the user's cache
        # Note: This may fail with 404 if the user has no graph data yet (new user)
        # That's fine - there's nothing to warm for new users
        try:
            await zep_client.graph.warm(user.graph_uuid)
            return {
                "status": "success",
                "user_uuid": user_uuid,
                "message": "User cache warmed successfully"
            }
        except Exception as warm_error:
            # Check if this is a "no graph data" error - that's expected for new users
            error_str = str(warm_error).lower()
            if "not found" in error_str or "graph data" in error_str:
                return {
                    "status": "success",
                    "user_uuid": user_uuid,
                    "message": "User is new - no cache to warm yet"
                }
            # Re-raise other errors
            raise

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error while warming cache")


@app.get("/")
async def root():
    """Root endpoint with usage info."""
    return {
        "service": "ElevenLabs-Zep Proxy",
        "description": "Custom LLM proxy that injects Zep context into every request",
        "endpoints": {
            "POST /v1/responses": "Main endpoint for ElevenLabs (Responses API)",
            "POST /create-user": "Create a Zep user and return its UUID",
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
    logger.info(f"  - POST http://localhost:{port}/v1/responses")
    logger.info(f"  - GET  http://localhost:{port}/health")

    uvicorn.run(app, host="0.0.0.0", port=port)
