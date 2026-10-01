# Changelog

All notable changes to the Zep eval harness are recorded in this file.

## [Unreleased]

### Changed

- The harness uses the Zep v4 Python SDK (`zep-cloud==4.0.0a5`).
- The harness addresses every user, thread, and graph by the server-generated
  UUID. The create calls do not send an application identifier. The run
  manifests and the checkpoints store the returned UUIDs.
- User ingestion sends conversation messages and telemetry through the v4 Batch
  API.
- Document ingestion sends each chunk through `graph.episode.add`.
- Evaluation retrieves context with `graph.get_context` and warms a graph with
  `graph.warm(graph_uuid)`.
- Graph inspection lists nodes, edges, and episodes with the v4 cursor pagers.
- The ontology and the instruction helpers use the `zep_cloud.ontology` module
  and the project-level or graph-level v4 methods.
- The `README.md` and the harness skill describe the v4 identifiers and the v4
  methods.

### Fixed

- The ingestion scripts print the success message only when all tasks
  succeeded. After a poll timeout, they print a warning with the number of
  completed tasks. After a failed task, they print the failure count. The
  manifest timing records the timeout in a `timed_out` field.
