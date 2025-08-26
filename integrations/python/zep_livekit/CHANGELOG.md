# Changelog

All notable changes to the zep-livekit integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-01-27

### Added
- Initial release of Zep LiveKit integration
- `ZepMemoryAgent` class extending LiveKit's Agent with Zep memory capabilities
- OpenAI provider integration (STT, LLM, TTS) with gpt-4o-mini default
- Dual memory strategy: thread-based conversation storage + graph-based knowledge extraction
- Automatic memory injection via `update_chat_ctx()` method
- User and thread-based memory isolation
- Memory management utilities in `zep_livekit.memory` module
- Complete voice assistant example with environment configuration
- Comprehensive test suite with mock client testing
- Development tooling: Makefile, linting, type checking, formatting
- Documentation and usage examples

### Features
- **Memory Storage**: Automatic conversation storage in Zep threads after each user turn
- **Context Injection**: Intelligent memory retrieval and injection before agent responses  
- **Graph Knowledge**: Entity and relationship extraction from voice interactions
- **Thread Continuity**: Resume conversations across sessions with thread_id parameter
- **Error Handling**: Comprehensive exception handling for memory operations
- **Production Ready**: Async/await throughout, proper logging, scalable architecture

### Dependencies
- `livekit-agents[openai]>=1.0.0` - LiveKit agents framework with OpenAI integration
- `zep-cloud>=3.0.0rc1` - Zep Cloud client library
- `typing-extensions>=4.0.0` - Type hints compatibility

[Unreleased]: https://github.com/getzep/zep/compare/zep-livekit-v0.1.0...HEAD
[0.1.0]: https://github.com/getzep/zep/releases/tag/zep-livekit-v0.1.0