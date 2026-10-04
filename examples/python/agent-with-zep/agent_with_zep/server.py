"""FastAPI server for the chat frontend.

POST /api/chat accepts a Vercel AI SDK UI message request and streams a Vercel
AI SDK UI message stream (data stream protocol, sdk_version=7). Per-request
toggles for domain_knowledge and planning are query parameters:
`/api/chat?domain_knowledge=false&planning=false`. Both default to true.

GET /api/orientation returns the cached ontology text and node sample.
GET /api/domain-knowledge returns the domain knowledge markdown.

Run: uv run uvicorn agent_with_zep.server:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic_ai.messages import ModelRequest
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from zep_cloud import AsyncZep

from .agent import build_agent, graph_sample_prompt
from .config import DATA_DIR, AgentConfig, Settings
from .orientation import load_orientation
from .tools import AgentDeps

app = FastAPI(title="agent-with-zep")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_settings: Settings | None = None
_zep: AsyncZep | None = None


def _settings_and_zep() -> tuple[Settings, AsyncZep]:
    global _settings, _zep
    if _settings is None:
        _settings = Settings.from_env()
        _zep = AsyncZep(
            api_key=_settings.zep_api_key,
            **({"base_url": _settings.zep_base_url} if _settings.zep_base_url else {}),
        )
    return _settings, _zep


@app.post("/api/chat")
async def chat(request: Request):
    settings, zep = _settings_and_zep()
    q = request.query_params
    config = AgentConfig(
        tools="full",
        orientation=True,
        domain_knowledge=q.get("domain_knowledge", "true").lower() != "false",
        planning=q.get("planning", "true").lower() != "false",
    )
    deps = AgentDeps(zep=zep, graph_id=settings.graph_id)
    agent = build_agent(config, settings.agent_model, deps)

    orientation = None
    if config.orientation:
        orientation = await load_orientation(zep, settings.graph_id)

    adapter = await VercelAIAdapter.from_request(request, agent=agent, sdk_version=7)
    # Inject the graph sample as graph data before the UI messages so the
    # frontend only ever sends chat messages.
    sample = graph_sample_prompt(deps, orientation)
    history = [ModelRequest.user_text_prompt(sample)] if sample else None
    stream = adapter.run_stream(deps=deps, message_history=history)
    return adapter.streaming_response(stream)


@app.get("/api/orientation")
async def get_orientation():
    settings, zep = _settings_and_zep()
    return JSONResponse(await load_orientation(zep, settings.graph_id))


@app.get("/api/domain-knowledge")
async def get_domain_knowledge():
    return {"domain_knowledge": (DATA_DIR / "domain_knowledge.md").read_text()}
