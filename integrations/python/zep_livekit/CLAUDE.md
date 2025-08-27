# Claude's Guide to Zep-LiveKit Integration Development

This document captures the complete development journey, architecture, and implementation details for the Zep-LiveKit integration project.

## Project Overview

**Goal**: Create a comprehensive Zep memory integration for LiveKit agents that provides persistent memory capabilities for voice AI applications.

**Repository**: `/Users/paulpaliychuk/job/zep/integrations/python/zep_livekit/`

**Key Achievement**: Successfully built a production-ready, dual-architecture memory system that provides both conversational memory and knowledge graph capabilities for LiveKit voice agents.

## What We Built

### 1. Dual Agent Architecture

**Two Specialized Agent Classes:**

- **`ZepUserAgent`** (`agent.py`): Thread-based conversational memory
  - Extends LiveKit's `Agent` class
  - Stores conversations in Zep threads using `thread.add_messages()`
  - Retrieves context using `thread.get_user_context()`
  - Perfect for personal assistant scenarios with conversation history
  - Supports context modes: "basic" or "summary"
  - Optional message naming for user and assistant attribution

- **`ZepGraphAgent`** (`agent.py`): Knowledge graph-based memory
  - Extends LiveKit's `Agent` class
  - Stores information in Zep knowledge graphs using `graph.add()`
  - Performs hybrid search across facts, entities, and episodes
  - Uses `compose_context_string()` for smart context composition
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
- Parallel graph search (edges, nodes, episodes) for knowledge memory
- Smart context composition using Zep's utility functions

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
# Storage
zep_message = Message(content=user_text.strip(), role="user", name=self._user_message_name)
await self._zep_client.thread.add_messages(thread_id=self._thread_id, messages=[zep_message])

# Retrieval
memory_result = await self._zep_client.thread.get_user_context(
    thread_id=self._thread_id, mode=self._context_mode
)
```

**Knowledge Graph Pattern (ZepGraphAgent):**
```python
# Storage with user attribution
if self._user_name:
    message_data = f"[{self._user_name}]: {user_text}"
await self._zep_client.graph.add(graph_id=self._graph_id, type="message", data=message_data)

# Hybrid retrieval
results = await asyncio.gather(
    graph.search(scope="edges", limit=facts_limit),
    graph.search(scope="nodes", limit=entity_limit),
    graph.search(scope="episodes", limit=episode_limit)
)
context = compose_context_string(edges, nodes, episodes)
```


## File Structure & Key Components

```
zep_livekit/
├── src/zep_livekit/
│   ├── __init__.py           # Exports ZepUserAgent, ZepGraphAgent
│   ├── agent.py              # Dual agent classes (424 lines)
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
    # Per-user agent instantiation
    user_id = extract_from_room_context(ctx)
    agent = ZepUserAgent(zep_client=zep_client, user_id=user_id, ...)
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