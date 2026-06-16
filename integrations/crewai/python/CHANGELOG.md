# Changelog

All notable changes to the zep-crewai package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.2] - 2026-06-16

### Changed
- Modernized for the latest dependencies: CrewAI 1.x and `zep-cloud>=3.23.0`.
- `ZepStorage`, `ZepUserStorage`, and `ZepGraphStorage` are now standalone,
  framework-agnostic Zep storage adapters. CrewAI 1.x removed
  `crewai.memory.storage.interface.Storage` (and the `ExternalMemory(storage=...)`
  wrapper / `external_memory=` Crew kwarg that consumed it), so these classes no
  longer subclass a CrewAI base. Their public `save(value, metadata)` /
  `search(query, limit, score_threshold)` / `reset()` API and Zep behavior
  (messages → `thread.add_messages`, data → `graph.add`, search →
  `thread.get_user_context` + `graph.search`) are preserved.
- Dependency-check import in `__init__.py` switched from the removed
  `crewai.memory.storage.interface` to `crewai.tools` (the supported extension
  point used by `ZepSearchTool` / `ZepAddDataTool`).
- Updated examples and README to wire Zep into CrewAI agents via the
  `ZepSearchTool` / `ZepAddDataTool` instead of the removed `ExternalMemory`.

### Removed
- Dropped the `mode` argument from `thread.get_user_context` calls. Zep V3 removed
  the thread context `mode` ("summary"/"basic") option and auto-assembles the
  Context Block.

### Deprecated
- `ZepUserStorage(mode=...)` is now accepted for backward compatibility but ignored,
  and emits a `DeprecationWarning`.

### Dependencies
- `zep-cloud` lower bound raised to `>=3.23.0`.
- `crewai` lower bound raised to `>=1.0.0`.
