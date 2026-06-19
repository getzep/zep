# Zep Integrations

Dedicated integration packages for using [Zep](https://www.getzep.com) agent memory with
AI frameworks. Each integration is packaged separately so you install only what you need.

Integrations are organized **framework-first, then language**: `integrations/<framework>/<language>/`.

New to Zep? Sign up at [getzep.com](https://www.getzep.com) and create an API key in the
[Zep dashboard](https://app.getzep.com); each package's `SETUP.md` has the details.

## Available integrations

| Framework | Language | Package | Location |
|-----------|----------|---------|----------|
| AG2 | Python | `zep-ag2` | [`ag2/python/`](ag2/python/) |
| CrewAI | Python | [`zep-crewai`](https://pypi.org/p/zep-crewai) | [`crewai/python/`](crewai/python/) |
| Google ADK | Python | [`zep-adk`](https://pypi.org/p/zep-adk) | [`adk/python/`](adk/python/) |
| Google ADK | TypeScript | `@getzep/zep-adk` | [`adk/typescript/`](adk/typescript/) |
| Google ADK | Go | `github.com/getzep/zep/integrations/adk/go` | [`adk/go/`](adk/go/) |
| LangGraph | Python | `zep-langgraph` | [`langgraph/python/`](langgraph/python/) |
| LiveKit | Python | [`zep-livekit`](https://pypi.org/p/zep-livekit) | [`livekit/python/`](livekit/python/) |
| Mastra | TypeScript | `@getzep/zep-mastra` | [`mastra/typescript/`](mastra/typescript/) |
| Microsoft Agent Framework | Python | `zep-ms-agent-framework` | [`ms-agent-framework/python/`](ms-agent-framework/python/) |
| Microsoft AutoGen | Python | [`zep-autogen`](https://pypi.org/p/zep-autogen) | [`autogen/python/`](autogen/python/) |
| Pydantic AI | Python | `zep-pydantic-ai` | [`pydantic-ai/python/`](pydantic-ai/python/) |
| Vercel AI SDK | TypeScript | `@getzep/zep-vercel-ai` | [`vercel-ai/typescript/`](vercel-ai/typescript/) |

## Package structure

See [`CLAUDE.md`](CLAUDE.md) for the full per-language structure and conventions. In short,
each package lives at `integrations/<framework>/<language>/` and ships a README, a `SETUP.md`,
a runnable example, tests, and a changelog.

## Development

Each package is built and tested independently. For a Python package:

```bash
cd integrations/<framework>/python
uv sync --extra dev      # install (dev extras)
uv run pytest            # test
uv build                 # build
```

TypeScript: `npm ci && npm test`. Go: `go test ./...`. See [`CLAUDE.md`](CLAUDE.md) for the
full per-language commands and the CI/release setup.

## Adding a new integration

1. Create `integrations/<framework>/<language>/` following the structure in `CLAUDE.md`.
2. Implement the framework's memory/context extension point; target the latest Zep SDK.
3. Add tests, a runnable example, a README, and a `SETUP.md`.
4. Wire CI: add a `paths-filter` entry in `.github/workflows/test-integrations.yml`.
5. Open a PR.

## Release

Each package releases independently via `.github/workflows/release-integrations.yml`, tag
scheme `zep-<framework>-<language>-v<version>` (Python → PyPI, TypeScript → npm; Go is
versioned by the module-path tag `integrations/<framework>/go/vX.Y.Z`).

## Support

- [Zep Documentation](https://help.getzep.com)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## Contributing

Contributions welcome — see the [Contributing Guide](../CONTRIBUTING.md).
