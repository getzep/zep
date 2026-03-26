# Changelog

All notable changes to the zep-ag2 package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - Unreleased

### Added
- Initial release of zep-ag2 integration package
- `ZepMemoryManager` for system message injection and conversation memory
- `ZepGraphMemoryManager` for knowledge graph operations
- Tool factories: `create_search_memory_tool`, `create_add_memory_tool`,
  `create_search_graph_tool`, `create_add_graph_data_tool`
- `register_all_tools` convenience function for bulk tool registration
- Sync tool execution via background event loop (AG2 calls tools synchronously)
- Async manager classes with sync wrappers for non-async usage
- Comprehensive test suite with >90% coverage
- Examples for basic, graph, search-only, and full tool usage

### Features
- AG2 decorator-compatible tools (`@register_for_llm` / `@register_for_execution`)
- System message enrichment with Zep memory context
- Thread-based conversation memory storage
- User and named knowledge graph support
- Typed parameters with `Annotated` for AG2 tool descriptions

[Unreleased]: https://github.com/getzep/zep/compare/zep-ag2-v0.1.0...HEAD
[0.1.0]: https://github.com/getzep/zep/releases/tag/zep-ag2-v0.1.0
