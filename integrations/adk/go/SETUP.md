# Setup Guide

This guide takes you from a fresh checkout to running the example agent with
Zep memory.

## 1. Sign up for Zep and create an API key

1. Go to [https://www.getzep.com](https://www.getzep.com) and create an account.
2. Open the [Zep dashboard](https://app.getzep.com) and select (or create) a project.
3. In the project settings, go to **API Keys** and create a new key.
4. Copy the key — you will set it as `ZEP_API_KEY` below.

Zep is a paid product; see [getzep.com](https://www.getzep.com) for plan details.

## 2. Get a Google API key (for the example)

The integration is model-agnostic, but the bundled example drives the agent with
Google's Gemini models — ADK for Go's native model provider. Create a key in
[Google AI Studio](https://aistudio.google.com/apikey) and copy it for
`GOOGLE_API_KEY`.

## 3. Install

Add the module to your project:

```bash
go get github.com/getzep/zep/integrations/adk/go@latest
```

Import it as `zepadk`:

```go
import zepadk "github.com/getzep/zep/integrations/adk/go"
```

To work from the repository instead:

```bash
git clone https://github.com/getzep/zep.git
cd zep/integrations/adk/go
go mod download
```

Requirements: Go 1.25+ (`google.golang.org/adk` v1.4.0 requires Go 1.25),
`google.golang.org/adk` v1.4.0, `github.com/getzep/zep-go/v3` v3.23.0.

## 4. Configure environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

If `ZEP_API_KEY` is unset, the integration disables itself (the Zep client is
`nil` and every Zep call becomes a no-op), so the agent still runs — useful for
confirming the wiring without a Zep account. If `GOOGLE_API_KEY` is unset, the
example prints the configured wiring and exits before calling the model.

## 5. Run the example

```bash
go run ./examples
```

The example:

1. Creates (idempotently) a Zep user and a thread keyed on the ADK session ID.
2. Builds an `llmagent` whose `BeforeModelCallback` persists each new user turn
   to Zep and injects the user's Context Block into the prompt, and whose
   `AfterModelCallback` persists the assistant's reply back to the same thread.
3. Registers a `search_memory` tool the model can call on demand and attaches a
   Zep-backed `memory.Service` at the runner.
4. Sends two turns and prints the agent's replies.

Because Zep ingestion is asynchronous, memory recall improves across turns and
sessions rather than instantly within the first turn.

## 6. Run the tests

The tests are mock/table-based and make no network calls, so no API keys are
required:

```bash
make test            # or: go test ./...
```

## Troubleshooting

- **`go get` cannot find the module** — the module path is
  `github.com/getzep/zep/integrations/adk/go`; Go modules under this subpath are
  tagged `integrations/adk/go/vX.Y.Z`.
- **Recall returns nothing** — Zep ingestion is asynchronous; a just-added fact
  is not instantly retrievable. Recall improves on subsequent turns and across
  sessions for the same user.
- **Authentication errors** — confirm `ZEP_API_KEY` is set in the same shell and
  belongs to the intended project.
- **Agent runs but has no memory** — verify `ZEP_API_KEY` is exported; an unset
  key makes the Zep client `nil` and all Zep calls no-ops by design.
