# Integration Package Workflows

GitHub Actions workflows for testing and releasing Zep integration packages.
Integrations are organized framework-first, then language: `integrations/<framework>/<language>/`.

## Workflows

### `test-integrations.yml` — PR / push testing
Detects which packages changed (via `dorny/paths-filter`) and tests them in three
per-language lanes:
- **`test-python`** — matrix Python 3.10–3.13; runs ruff (lint + format check), mypy, and
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
- **TypeScript → npm** (`npm ci` → build → test → `npm publish`, using `NPM_TOKEN`).

**Tag scheme:** `zep-<framework>-<language>-v<version>` — e.g. `zep-adk-python-v0.2.0`,
`zep-mastra-typescript-v0.1.0`. Manual dispatch takes `framework` + `language` inputs.

**Go modules** are not released by this workflow — a Go module is versioned by a git tag
matching its module subpath: `integrations/<framework>/go/vX.Y.Z`, consumed via
`go get github.com/getzep/zep/integrations/<framework>/go@vX.Y.Z`.

## Setup requirements

### PyPI (trusted publishing)
For each Python package (e.g. `zep-adk`): add a GitHub publisher in the PyPI project
settings with repository `getzep/zep`, workflow `release-integrations.yml`, and environment
`release`. Create a `release` environment in the repository settings (add protection rules
as desired). No secret needed — trusted publishing uses OIDC.

### npm
Add an `NPM_TOKEN` secret (an automation token with publish rights) for TypeScript packages.

## Adding a new package

1. Create `integrations/<framework>/<language>/` per [`../../integrations/CLAUDE.md`](../../integrations/CLAUDE.md).
2. Add a `paths-filter` entry under the matching language in `test-integrations.yml`
   (e.g. `pydantic-ai: ['integrations/pydantic-ai/python/**']`).
3. Python: configure PyPI trusted publishing. TypeScript: ensure `NPM_TOKEN` is set.
4. Release by tagging `zep-<framework>-<language>-v<version>` (or the Go module-path tag).

## Troubleshooting

- **Package not detected:** verify the `paths-filter` entry matches the package directory.
- **Tests fail on PR:** check dependencies and language/version compatibility.
- **Release fails:** confirm PyPI trusted publishing (env `release`) or `NPM_TOKEN` is set.
