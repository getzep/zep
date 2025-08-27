# Changelog

All notable changes to the zep-livekit integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-01-27

### Added
- Initial release of Zep LiveKit integration
- **Dual Agent Architecture**:
  - `ZepUserAgent`: Thread-based conversational memory for user sessions
  - `ZepGraphAgent`: Knowledge graph-based memory for shared knowledge across sessions
- **Event-Driven Architecture**: Automatic conversation capture using LiveKit's conversation events
- **Message Attribution**: Optional user and assistant message naming for better conversation tracking
- **Hybrid Memory Retrieval**: Graph agent supports parallel search across facts, entities, and episodes
- **User Prefixing**: Graph agent supports optional user name prefixing for multi-user attribution

### Core Features
- **Thread Memory**: Persistent conversation history in Zep threads with context modes (basic/summary)
- **Knowledge Graph**: Shared knowledge storage across conversations with smart context composition
- **Memory Injection**: Automatic context retrieval and injection into LiveKit agent conversations
- **Message Deduplication**: Prevents duplicate message storage using content hashing and IDs
- **Error Handling**: Comprehensive exception handling with graceful degradation
- **Type Safety**: Full type annotations and MyPy compatibility throughout

### Integration Capabilities
- **LiveKit Compatibility**: Drop-in replacement for standard LiveKit Agent
- **Flexible Constructor**: Dynamic `**kwargs` support for all LiveKit Agent parameters
- **Tool Integration**: Function tools that can be mixed into any LiveKit agent
- **OpenAI Integration**: Seamless compatibility with LiveKit's OpenAI plugins
- **Production Ready**: Async/await throughout, proper logging, minimal overhead

### Examples & Documentation
- **Voice Assistant Example**: Complete thread-based memory agent (`voice_assistant.py`)
- **Knowledge Assistant Example**: Graph-based memory agent (`graph_voice_assistant.py`)
- **Tools Examples**: Standalone memory tools integration examples
- **Deployment Guide**: FastAPI and production deployment patterns
- **Comprehensive Documentation**: API reference, usage patterns, and best practices

### Development Infrastructure
- **Quality Assurance**: Ruff formatting, MyPy type checking, comprehensive linting
- **Clean Architecture**: Separation of concerns between storage and retrieval
- **Makefile Workflows**: `make pre-commit`, `make ci` for development consistency
- **No-Test Mode**: Graceful handling of projects without test files

### Dependencies
- `livekit-agents>=0.8.0` - LiveKit agents framework
- `zep-cloud>=3.4.3` - Zep Cloud client library  
- `typing-extensions>=4.0.0` - Type hints compatibility

### Architecture Decisions
- **Event-Driven Storage**: Uses LiveKit's `conversation_item_added` events for real-time capture
- **Dual Memory Strategy**: Thread memory for conversations, graph memory for knowledge
- **Per-User Agent Instances**: Designed for typical deployment where each user gets their own agent
- **Minimal Logging**: Clean, production-ready logging with essential information only

[Unreleased]: https://github.com/getzep/zep/compare/zep-livekit-v0.1.0...HEAD
[0.1.0]: https://github.com/getzep/zep/releases/tag/zep-livekit-v0.1.0