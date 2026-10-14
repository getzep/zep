# Changelog

## [0.3.0] - 2026-10-14

### Changed

- Migrated the package to `zep-cloud>=4.0.0,<5`.
- Replaced `ensure_user` and `ensure_thread` with `create_user` and
  `create_thread`. Zep assigns resource UUIDs, and the helpers return the
  created SDK resources.
- Changed agent and search-tool inputs to `user_uuid`, `thread_uuid`, and
  `graph_uuid`.
- Changed graph storage to `graph.episode.add` and graph context retrieval to
  `graph.get_context`. Added support for a graph context `template_uuid`.
- Changed graph search to the V4 scope-specific search methods. The `auto`
  scope uses `graph.get_context`.
- Updated examples and setup documentation for the V4 API.

### Removed

- Removed caller-selected resource IDs, lazy resource creation, and V3
  conflict handling. Create resources before you start the agent.
- Removed V3 `context_mode`, graph search limits for separate result groups,
  and V3 hybrid-search composition.
