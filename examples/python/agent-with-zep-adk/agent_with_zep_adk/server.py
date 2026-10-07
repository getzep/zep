"""FastAPI server for the shared chat frontend.

Run: uv run uvicorn agent_with_zep_adk.server:app --reload
"""

from __future__ import annotations

from collections import OrderedDict
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from google.adk.agents import RunConfig
from google.adk.agents.run_config import StreamingMode
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from zep_cloud import AsyncZep

from .agent import build_agent, graph_sample_prompt, run_events
from .config import DATA_DIR, AgentConfig, Settings
from .handles import HandleRegistry
from .models import resolve_model
from .orientation import load_orientation
from .tools import AgentDeps
from .ui_stream import encode_ui_stream

app = FastAPI(title="agent-with-zep-adk")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_settings: Settings | None = None
_zep: AsyncZep | None = None
_registries: OrderedDict[str, HandleRegistry] = OrderedDict()
MAX_CHAT_REGISTRIES = 100


def _get_registry(chat_id: str | None) -> HandleRegistry:
    if chat_id is None:
        return HandleRegistry()
    registry = _registries.get(chat_id)
    if registry is None:
        registry = HandleRegistry()
        _registries[chat_id] = registry
    _registries.move_to_end(chat_id)
    if len(_registries) > MAX_CHAT_REGISTRIES:
        _registries.popitem(last=False)
    return registry


def _settings_and_zep() -> tuple[Settings, AsyncZep]:
    global _settings, _zep
    if _settings is None:
        _settings = Settings.from_env()
        _zep = AsyncZep(
            api_key=_settings.zep_api_key,
            **({"base_url": _settings.zep_base_url} if _settings.zep_base_url else {}),
        )
    return _settings, _zep


def _message_text_parts(message: dict) -> list[types.Part]:
    return [
        types.Part(text=part["text"])
        for part in message.get("parts", [])
        if part.get("type") == "text" and isinstance(part.get("text"), str)
    ]


@app.post("/api/chat")
async def chat(request: Request):
    settings, zep = _settings_and_zep()
    body = await request.json()
    chat_id = body.get("id")
    registry = _get_registry(chat_id if isinstance(chat_id, str) else None)
    registry.reset_seen()
    messages = body.get("messages", [])
    last_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        None,
    )
    if last_user_index is None:
        raise HTTPException(status_code=400, detail="The request must include a user message.")

    query = request.query_params
    config = AgentConfig(
        tools="full",
        orientation=True,
        domain_knowledge=query.get("domain_knowledge", "true").lower() != "false",
        planning=query.get("planning", "true").lower() != "false",
    )
    deps = AgentDeps(zep=zep, graph_id=settings.graph_id, registry=registry)
    orientation = await load_orientation(zep, settings.graph_id)
    sample = graph_sample_prompt(deps, orientation)
    model, generate_content_config = resolve_model(settings.agent_model, settings.model_thinking)
    agent = build_agent(
        config,
        deps,
        model=model,
        generate_content_config=generate_content_config,
    )

    session_service = InMemorySessionService()
    runner = Runner(
        app_name="agent_with_zep_adk",
        agent=agent,
        session_service=session_service,
    )
    session = await session_service.create_session(
        app_name="agent_with_zep_adk",
        user_id=f"chat-{uuid4()}",
    )
    if sample:
        await session_service.append_event(
            session,
            Event(
                invocation_id=uuid4().hex,
                author="user",
                content=types.Content(role="user", parts=[types.Part(text=sample)]),
            ),
        )
    for message in messages[:last_user_index]:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        parts = _message_text_parts(message)
        if not parts:
            continue
        await session_service.append_event(
            session,
            Event(
                invocation_id=uuid4().hex,
                author="user" if role == "user" else agent.name,
                content=types.Content(
                    role="user" if role == "user" else "model",
                    parts=parts,
                ),
            ),
        )

    last_user_parts = _message_text_parts(messages[last_user_index])
    if not last_user_parts:
        raise HTTPException(status_code=400, detail="The last user message must contain text.")
    event_stream = run_events(
        runner,
        user_id=session.user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=last_user_parts),
        deps=deps,
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    )
    return StreamingResponse(
        encode_ui_stream(event_stream, message_id=uuid4().hex),
        media_type="text/event-stream",
        headers={"x-vercel-ai-ui-message-stream": "v1"},
    )


@app.get("/api/orientation")
async def get_orientation():
    settings, zep = _settings_and_zep()
    return JSONResponse(await load_orientation(zep, settings.graph_id))


@app.get("/api/domain-knowledge")
async def get_domain_knowledge():
    return {"domain_knowledge": (DATA_DIR / "domain_knowledge.md").read_text()}
