# Repository Workflows

GitHub Actions workflows for testing and releasing Zep integration packages, the
`zep-ingest` package, and the agent plugins under `plugins/`.
Integrations are organized framework-first, then language: `integrations/<framework>/<language>/`.

## Workflows

### `codex-code-review.yml` — automatic maintainer PR review

Runs Codex when a non-draft pull request is opened, updated, reopened, or
marked ready for review. A secretless preflight job verifies the PR comes from
a branch in this repository, its author is not a bot, and the author has write
(or higher) repository permission; anything else is skipped before the
secret-bearing job starts. Codex's output is posted as a GitHub pull request
review. It does not submit a formal approval.

Requires an `OPENAI_API_KEY` Actions secret.

### `test-integrations.yml` — PR / push testing
Detects which packages changed (via `dorny/paths-filter`) and tests them in three
per-language lanes:
- **`test-python`** — matrix Python 3.11–3.13; runs ruff (lint + format check), mypy, and
  pytest with coverage (`.github/actions/test-python`).
- **`test-typescript`** — matrix Node 20/22; runs `npm ci`, lint, typecheck, `npm test`
  (`.github/actions/test-typescript`).
- **`test-go`** — `go vet`, `go test`, golangci-lint (`.github/actions/test-go`).

Each composite action derives the package directory (`integrations/<key>/<language>`) from
the `package` input; the Python action also derives the import name
(`zep_<key with hyphens→underscores>`).

**Triggers:** pull requests and pushes to `main` with changes under `integrations/**`.

### `release-integrations.yml` — package releases
Triggered by a published GitHub release or manual dispatch. Routes by language:
- **Python → PyPI** (test → build → publish via trusted publishing / OIDC).
- **TypeScript → npm** (`npm ci` → build → test → `npm publish`, via trusted
  publishing / OIDC).
- **Go → GitHub + Go proxy** (manual dispatch → test → scoped tag + GitHub Release →
  `proxy.golang.org` verification).

**Tag scheme:** `zep-<framework>-<language>-v<version>` — e.g. `zep-adk-python-v0.2.0`,
`zep-mastra-typescript-v0.1.0`. Manual dispatch takes `framework` + `language` inputs.

**Go modules** use a git tag matching the module subpath:
`integrations/<framework>/go/vX.Y.Z`. To release one, run this workflow manually from
`main`, choose the framework and `go`, and enter the version without the `v` prefix. After
the Go checks pass and the protected `release` environment is approved, the workflow
creates the scoped tag and GitHub Release and verifies the module through the public Go
proxy. For example, ADK version `0.1.0` is consumed with
`go get github.com/getzep/zep/integrations/adk/go@v0.1.0`.

### `release-ingestion.yml` — ingestion package releases

Triggered only by manual dispatch from `main`, with no inputs required. The
workflow reads the version from `ingestion/pyproject.toml`, resolves and
archives the exact commit once, tests that source on Python 3.11–3.13, and
builds it. After the `release` environment is approved, it creates the
`zep-ingest-v<version>` tag and GitHub Release, then publishes `zep-ingest` to
PyPI through trusted publishing.

### `test-plugins.yml` — plugin manifest consistency

Runs on pull requests that touch `plugins/**`, the path-scoped Claude rule, or
any of the three marketplace manifests — and only there; there is no post-merge
run.
`scripts/plugin_manifests.py --check` asserts the two values `building-with-zep` is
forced to duplicate, because it ships as one directory published into three
ecosystems (Claude Code, Codex, Cursor):

- **The release version**, in the three ecosystem plugin manifests. Claude Code
  uses it for update detection, while all three ecosystems expose the same version
  as the plugin's release and support identity.
- **The `zep-docs` MCP endpoint**, in `.mcp.json` (Claude, Codex) and `mcp.json`
  (Cursor).

The check also fails if a `version` appears in any marketplace entry. Each
ecosystem reads its version from its own `plugin.json`; a marketplace copy is
redundant and can drift.

A second step verifies that the nested `AGENTS.md` and Claude's path-scoped rule
contain exactly the same instructions. A third step fails when **the plugin's
loaded content changes without a semantic version increase** (`README.md`,
`CHANGELOG.md`, and the maintainer-only `AGENTS.md` are exempt). Without the bump
check, a PR can change the skill and pass every check while Claude Code keeps
serving the cached release, which is what happened to #566 and #567 — both
shipped under `0.1.0`. It diffs against the base branch, so the job checks out
with `fetch-depth: 0`.

All three steps are **advisory**. Like the other path-filtered test workflows here, this
is not a required status check: a failure shows as a red check on the pull request
and does not disable the merge button. It warns the author before merge; it is not a
gate.

Plugins have no publish step — the marketplace is this git repository, so merging
to `main` is the release. There is no `release-plugins.yml`. See
[`plugins/building-with-zep/README.md#releasing`](../../plugins/building-with-zep/README.md#releasing)
for the release procedure.

## Setup requirements

### PyPI (trusted publishing)
For each Python package (e.g. `zep-adk`): add a GitHub publisher in the PyPI project
settings with repository `getzep/zep`, workflow `release-integrations.yml`, and environment
`release`. Create a `release` environment in the repository settings (add protection rules
as desired). No secret needed — trusted publishing uses OIDC.

For `zep-ingest`, configure the publisher with workflow
`release-ingestion.yml` and the same `release` environment.

### npm
For each TypeScript package (e.g. `@getzep/zep-adk`): add a GitHub Actions trusted
publisher in the npm package settings:

- Organization or user: `getzep`
- Repository: `zep`
- Workflow filename: `release-integrations.yml`
- Environment name: `release`
- Allowed actions: `npm publish`

No secret is needed — trusted publishing uses OIDC. npm requires the package to already
exist before configuring a trusted publisher, so new package names need a one-time initial
publish by a maintainer with npm access before switching subsequent releases to this
workflow.

## Adding a new package

1. Create `integrations/<framework>/<language>/` per [`../../integrations/CLAUDE.md`](../../integrations/CLAUDE.md).
2. Add a `paths-filter` entry under the matching language in `test-integrations.yml`
   (e.g. `pydantic-ai: ['integrations/pydantic-ai/python/**']`).
3. Python: configure PyPI trusted publishing. TypeScript: configure npm trusted publishing.
4. Python/TypeScript: publish a release tag using `zep-<framework>-<language>-v<version>`.
   Go: manually dispatch `release-integrations.yml`; do not create the tag yourself.

## Troubleshooting

- **Package not detected:** verify the `paths-filter` entry matches the package directory.
- **Tests fail on PR:** check dependencies and language/version compatibility.
- **Release fails:** confirm PyPI or npm trusted publishing is configured with environment
  `release`.
- **Go tag already exists:** versions are immutable. Re-run only when the tag points to the
  same commit; otherwise choose a new version.
