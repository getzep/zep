# Zep Go SDK v3.17.0 Delta

Latest verified version: `github.com/getzep/zep-go/v3 v3.17.0` published on 2026-03-02.

## Current MCP impact

- `GraphSearchQuery.MinFactRating` was removed in `v3.17.0`.
  The MCP `search_graph` tool must not accept or send `min_fact_rating`.
- `ThreadGetUserContextRequest.MinRating` and `ThreadGetUserContextRequest.Mode` were removed.
  The MCP server already does not expose them, so no code change is needed.
- `MessageListResponse.ThreadCreatedAt` was added.
  `get_thread_messages` already returns the raw SDK response, so the field is available automatically.

## New SDK capabilities

- `client.Graph.DetectPatterns(...)` added.
  This is a read-only graph analysis API for relationships, paths, co-occurrences, hubs, and clusters.
- `client.Graph.Node.Update(...)` added.
- `client.Graph.Edge.Update(...)` added.

## MCP changes implemented

- Added a read-only `detect_patterns` tool.
- Documented `thread_created_at` in `get_thread_messages` output.
- Did not add `update_node` or `update_edge`.
  The MCP server is explicitly read-only and these endpoints are mutating operations.

## Sources

- Go module resolution: `go list -m -json github.com/getzep/zep-go/v3@latest`
- SDK docs: https://help.getzep.com/sdk-reference/graph/detect-patterns-experimental
