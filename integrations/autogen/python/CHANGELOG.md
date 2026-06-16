# Changelog

All notable changes to the zep-autogen package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.1]

### Changed
- Target the latest Zep SDK (`zep-cloud>=3.23.0`) and AutoGen (`autogen-agentchat`/`autogen-ext>=0.7.0`).
- `ZepUserMemory.update_context` no longer passes the removed `mode` argument to
  `thread.get_user_context`. In Zep V3 the Context Block is auto-assembled and the
  `"basic"`/`"summary"` modes have been deprecated and removed.

### Removed
- Dropped the `thread_context_mode` constructor parameter (no longer supported by the
  Zep V3 API).

### Added
- New optional `context_template_id` constructor parameter on `ZepUserMemory`. When set,
  it is forwarded as `template_id` to `thread.get_user_context`, enabling custom Context
  Block rendering via Zep context templates (the V3 replacement for summary-vs-raw control).

## [0.1.0] - 2024-01-XX

### Added
- Initial release of zep-autogen integration package
- `ZepMemory` class implementing AutoGen's Memory interface
- Support for persistent conversation memory with Zep Cloud
- Async/await support for modern Python applications
- Comprehensive type hints and documentation
- Basic example demonstrating usage with AutoGen agents
- Error handling for missing dependencies

### Features
- Seamless integration with AutoGen agents
- Intelligent context retrieval from Zep memory
- Support for user-specific and thread-specific memory contexts
- Configurable memory retrieval limits
- Compatible with AutoGen 0.6.1+

[Unreleased]: https://github.com/getzep/zep/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/getzep/zep/releases/tag/v0.1.0