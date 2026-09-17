# Claude's Guide to Zep-LiveKit Integration Development

This document captures the complete development journey, architecture, and implementation details for the Zep-LiveKit integration project.

## Project Overview

**Goal**: Create a comprehensive Zep memory integration for LiveKit agents that provides persistent memory capabilities for voice AI applications.

**Repository**: `integrations/livekit/python/`

**Key Achievement**: Successfully built a production-ready, dual-architecture memory system that provides both conversational memory and knowledge graph capabilities for LiveKit voice agents.

## What We Built

### 1. Dual Agent Architecture

**Two Specialized Agent Classes:**

- **`ZepUserAgent`** (`agent.py`): Thread-based conversational memory
  - Extends LiveKit's `Agent` class
  - Stores conversations in Zep threads using `thread.add_messages(thread_uuid, ...)`
  - Retrieves context from `thread.add_messages(return_context=True)`, or from
    `thread.get_context(thread_uuid)`
  - Takes `user_uuid` and `thread_uuid`, because Zep v4 addresses a resource by a
    server-generated UUID
  - Perfect for personal assistant scenarios with conversation history
  - Optional message naming for user and assistant attribution

- **`ZepGraphAgent`** (`agent.py`): Knowledge graph-based memory
  - Extends LiveKit's `Agent` class
  - Stores information in Zep knowledge graphs using `graph.episode.add(graph_uuid, ...)`
  - Retrieves an assembled context block with `graph.get_context(graph_uuid, ...)`
  - Takes `graph_uuid`
  - Perfect for shared knowledge scenarios across multiple users
  - Optional user name prefixing for message attribution


### 2. Key Features Implemented

**Event-Driven Architecture:**
- Uses LiveKit's `conversation_item_added` events for real-time capture
- Automatic conversation capture without manual intervention
- Message deduplication using content hashing and message IDs
- Proper role-based message categorization (user/assistant)

**Memory Storage:**
- Thread-based conversation history storage in `ZepUserAgent`
- Knowledge graph storage in `ZepGraphAgent` with user attribution
- Message attribution with optional user/assistant names
- Error handling with graceful degradation

**Memory Retrieval:**
- Context-aware memory injection in `on_user_turn_completed`
- Thread context retrieval for conversational memory
- Server-side context assembly with `graph.get_context` for knowledge memory
- Dedicated v4 search methods (`graph.search_edges`, `graph.search_nodes`,
  `graph.search_episodes`, `graph.search_observations`,
  `graph.search_thread_summaries`) in the model-callable search tool. Each method
  returns a pager, so the caller reads `.items` or iterates the pager.

**LiveKit Integration:**
- Full compatibility with LiveKit Agent ecosystem
- Support for all Agent parameters (STT, LLM, TTS, VAD, tools, etc.)
- Dynamic constructor with `**kwargs: Any` for future-proofing
- Drop-in replacement for standard LiveKit agents

## Development Journey & Problem Solving

### Initial Challenge: Memory Integration Pattern
- **Problem**: How to integrate persistent memory with LiveKit's real-time voice framework
- **Solution**: Event-driven architecture using LiveKit's conversation events
- **Result**: Seamless integration that captures conversations automatically

### Architecture Evolution
- **First Approach**: Single agent class with mixed responsibilities
- **Issue**: Complex codebase with unclear separation of concerns
- **Final Solution**: Dual agent architecture + standalone tools
- **Benefits**: Clear separation, flexible usage patterns, maintainable code

### Message Attribution Requirements
- **Need**: Better tracking of who said what in conversations
- **Implementation**: Optional message naming parameters
- **Result**: `user_message_name` and `assistant_message_name` parameters in `ZepUserAgent`

### Multi-User Considerations
- **Research**: Investigated LiveKit's multi-user capabilities
- **Finding**: Agents are typically instantiated per-user, not as shared instances
- **Solution**: Simple user name prefixing in `ZepGraphAgent` for attribution
- **Deployment Pattern**: Per-user agent instances in production environments


## Current Implementation Status

### ✅ Completed Features
1. **Dual agent architecture** - Thread-based and graph-based memory
2. **Event-driven conversation capture** - Real-time message storage
3. **Memory context injection** - Automatic context retrieval and injection
4. **LiveKit compatibility** - Full Agent ecosystem integration
5. **Message deduplication** - Prevents duplicate storage
6. **Error handling & logging** - Production-ready reliability
7. **Type safety** - Full typing support with proper inheritance
8. **Message attribution** - Optional naming for better conversation tracking
9. **Clean architecture** - Separation between storage and retrieval concerns

### 🏗️ Architecture Patterns

**Thread Memory Pattern (ZepUserAgent):**
```python
# Storage and retrieval in one round-trip
zep_message = AddMessage(content=user_text.strip(), role="user", name=self._user_message_name)
response = await self._zep_client.thread.add_messages(
    self._thread_uuid, messages=[zep_message], return_context=True
)
context = response.context
```

**Knowledge Graph Pattern (ZepGraphAgent):**
```python
# Storage with user attribution
if self._user_name:
    message_data = f"[{self._user_name}]: {user_text}"
await self._zep_client.graph.episode.add(
    self._graph_uuid, type="message", data=message_data
)

# Retrieval: Zep assembles the context block on the server
response = await self._zep_client.graph.get_context(
    self._graph_uuid,
    query=query,
    filters=self._search_filters,
    max_characters=self._max_characters,
)
context = response.context
```


## File Structure & Key Components

```
zep_livekit/
├── src/zep_livekit/
│   ├── __init__.py           # Exports ZepUserAgent, ZepGraphAgent
│   ├── agent.py              # Dual agent classes
│   ├── provisioning.py       # create_user / create_thread helpers
│   ├── tools.py              # create_graph_search_tool
│   ├── limits.py             # payload truncation
│   └── exceptions.py         # Custom exception classes
├── examples/
│   ├── voice_assistant.py    # ZepUserAgent example with thread memory
│   └── graph_voice_assistant.py  # ZepGraphAgent example with graph memory
├── README.md                 # Comprehensive usage documentation
├── CHANGELOG.md              # Detailed version history
├── pyproject.toml            # Package configuration
└── Makefile                  # Development workflow commands
```

## Production Deployment Patterns

### FastAPI Integration Pattern
```python
# FastAPI creates room and tokens
@app.post("/create-room/{user_id}")
async def create_voice_session(user_id: str, user_name: str):
    # Generate access token and room for user
    
# Separate agent worker process
async def entrypoint(ctx: agents.JobContext):
    # Per-user agent instantiation. The application stores the Zep UUIDs in its
    # own database and reads them here. The agent does not resolve a name.
    user_uuid, thread_uuid = lookup_stored_uuids(ctx)
    agent = ZepUserAgent(
        zep_client=zep_client, user_uuid=user_uuid, thread_uuid=thread_uuid, ...
    )
```

### Deployment Environments
- **LiveKit Cloud**: Managed deployment with `livekit-cli deploy`
- **Self-Hosted**: Docker containers with Kubernetes scaling
- **Hybrid**: FastAPI web layer + LiveKit agent workers

## Quality Standards Achieved

✅ **Code Quality**: All linting (ruff), type checking (MyPy), and formatting passing  
✅ **Architecture**: Clean separation of concerns with dual approach  
✅ **Reliability**: Comprehensive error handling and graceful degradation  
✅ **Maintainability**: Well-documented, typed, and structured codebase  
✅ **Compatibility**: Full LiveKit Agent ecosystem integration  
✅ **Flexibility**: Both wrapper agents and standalone tools available  
✅ **Performance**: Event-driven, non-blocking operations throughout  

## Success Metrics

The Zep-LiveKit integration successfully provides:
- **Two complementary memory approaches** for different use cases
- **Production-ready reliability** with comprehensive error handling
- **Full LiveKit compatibility** as drop-in Agent replacements
- **Type safety and maintainability** with comprehensive type annotations
- **Clean architecture** with clear separation of concerns
- **Easy deployment** following standard LiveKit patterns

## Ready for Production

The integration is **production-ready** with comprehensive documentation, examples, error handling, and follows industry best practices for both LiveKit agents and Zep memory integration.