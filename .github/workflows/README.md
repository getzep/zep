# Integration Package Workflows

GitHub Actions workflows for testing and releasing Zep integration packages.
Integrations are organized framework-first, then language: `integrations/<framework>/<language>/`.

## Workflows

### `codex-code-review.yml` — automatic maintainer PR review

Runs Codex when a non-draft pull request authored by a `getzep` organization
member is opened, updated, reopened, or marked ready for review. Pull requests
from outside contributors and external collaborators are skipped before the
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

## Setup requirements

### PyPI (trusted publishing)
For each Python package (e.g. `zep-adk`): add a GitHub publisher in the PyPI project
settings with repository `getzep/zep`, workflow `release-integrations.yml`, and environment
`release`. Create a `release` environment in the repository settings (add protection rules
as desired). No secret needed — trusted publishing uses OIDC.

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
