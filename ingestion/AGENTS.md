# AGENTS.md — zep-ingest

Instructions for AI agents working in `ingestion/`. The `README.md` is written
for users of the package; this file is the contract for people and agents who
change it.

## What this package is

`zep-ingest` is a Python library that takes documents, JSON records, Slack
exports, transcripts, emails, thread messages, fact triples, and nodes and
ingests them into a Zep Context Graph. It targets the Zep v4 API and the
`zep-cloud` 4.x Python SDK (`zep-cloud>=4.0.0,<5`). There is no v3
compatibility path.

## Layout

- `src/zep_ingest/` — the package. `pipeline.py` wires
  loader → transforms → LimitGuard → submitter; `submitters/batch.py` and
  `submitters/sequential.py` are the two submission paths; `result.py` is the
  `IngestResult` status/polling surface; `verify.py` is `search_when_ready`.
- `tests/` — pytest. `tests/test_integration.py` is marked `integration` and
  runs only when `ZEP_API_KEY` is set.
- `examples/` — self-contained runnable examples against bundled sample data.
- `scripts/release_smoke_prod.py` — the live production smoke suite.

## Development

Python ≥ 3.11, `uv` for the environment:

```bash
make install   # uv sync --extra dev
make all       # format + lint + type-check + test — run before every commit
```

Individual targets: `make format`, `make lint`, `make type-check`, `make test`.
`make pre-commit` auto-fixes then runs the checks.

## V4 API rules — do not violate these

- **Never send a client-chosen identifier.** Do not pass `user_id`,
  `thread_id`, `graph_id`, or `target_graph_id` on any create call — v4
  rejects them. `user.create` accepts an optional `user_id` for IdP sign-in
  matching only; this package never sets it. Create the resource once, store
  the returned UUID, and pass the UUID on every later call.
- **No `lookup`.** `user.lookup` / `thread.lookup` / `graph.lookup` are for a
  one-time V3 backfill only. Never call them in package code or use them as a
  fallback for a missing UUID.
- **`reference_time`, not `created_at`.** The developer-supplied timestamp
  travels on the wire as `reference_time` on `AddMessage`, `BatchItemInput`,
  and `graph.episode.add`. The package's `Episode.created_at` and
  `ThreadMessage.created_at` fields map onto it in `types.py` and
  `threads.py`.
- **`page_size`, not `limit`,** on every list and search call.
- **`graph.edge.add`** takes `edges=[EdgeInput(...)]` plus `deduplicate=True`
  (that flag preserves the v3 `add_fact_triple` behavior) and returns
  `AddEdgesResult`.
- **Read `rejected_items`.** `batch.add_items` returns a
  `BatchItemsResponse` whose `rejected_items` lists items rejected by index
  and reason inside an otherwise-successful response. `add_items_page` in
  `submitters/batch.py` subtracts them from the submitted count and records an
  `AddError` — never count a whole page as submitted without checking.
- **Batch fallback is endpoint-404 only.** `method="auto"` falls back from
  batch to sequential only when the batch endpoint itself is unavailable
  (HTTP 404). Any other error (auth, quota, validation) is surfaced, never
  silently downgraded.
- **Restricted surfaces.** Never call `agent.*`, `trace_connection.*`,
  `graph.hyperedge.*` (or `hyperedges_enabled` on `project.update`), or the
  `content_policy` operations — they are not part of the v4 migration.
- **SDK only.** Use `zep-cloud` for every Zep API call. Never make raw HTTP
  requests to the Zep API; if a capability exists only on the HTTP API, stop
  and flag it.

## Testing

- Run `make all` before committing. The unit suite must stay green.
- Live integration tests run only when `ZEP_API_KEY` is set; they create and
  delete throwaway graphs. Do not skip or `xfail` a test because of a backend
  defect — let it fail and report it. Skips are only for missing
  credentials or account features.
- Use the official SDKs and real API keys for live checks. For development,
  `ZEP_API_URL=https://api.development.getzep.com` with a development key;
  the v4 SDK reads the base URL at client construction.
- Run examples end to end when a change touches a path they exercise:
  `cd examples && python <example>.py` with `ZEP_API_KEY` set
  (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` only enrich `documents_example.py`).

## Releasing

Version lives in `pyproject.toml` (`project.version`), the `__version__`
fallback in `src/zep_ingest/__init__.py`, and the top heading of
`CHANGELOG.md` — keep all three in sync and put breaking changes under a
`Breaking` heading.

Release procedure:

1. Land changes on `main` (no direct pushes; use a branch).
2. Bump the version in the three places above and update the CHANGELOG.
3. Run `make all`.
4. **Run the production smoke suite locally and confirm it passes** — the
   release workflow does not run it:

   ```bash
   cd ingestion
   ZEP_API_URL=https://api.getzep.com uv run python scripts/release_smoke_prod.py
   ```

   It needs `ZEP_API_KEY` set to a production key. Use your own key (or
   `keybank run zep-prod -- env ...`).
5. Dispatch `release-ingestion.yml` manually from `main`. It tests on Python
   3.11–3.13, builds, creates the `zep-ingest-v<version>` tag and GitHub
   Release, and publishes to PyPI via trusted publishing. See
   `.github/workflows/README.md` for the full contract.

## Commit style

Write commit messages in Simplified Technical English: complete sentences with
articles, imperative mood for the change itself.
