# Changelog

## 0.1.0 (2026-08-13)

### Added

- DeepSeek Harness Cordis plugin and installable bundle.
- Context Block recall through `agent/pre-step`, recorded as durable
  source-attributed context.
- Completed-turn persistence through `session/event`, excluding injected
  context, tool results, and intermediate assistant tool-call preambles.
- Lazy user/thread provisioning, per-session thread derivation, configurable
  identity and context formatting, duplicate-write protection, and fail-open
  Zep error handling.
- Programmatic `ZepMemoryRuntime` and `installZepMemory` APIs.
