# Setup Guide

This guide explains how to run the Go example with Zep.

## 1. Create a Zep API key

1. Create an account at [getzep.com](https://www.getzep.com).
2. Open your project in the [Zep dashboard](https://app.getzep.com).
3. Open **API Keys** in the project settings.
4. Create an API key and set it as `ZEP_API_KEY`.

## 2. Create a Google API key

The example uses Google's Gemini model through ADK. Create a key in
[Google AI Studio](https://aistudio.google.com/apikey) and set it as
`GOOGLE_API_KEY`.

## 3. Install the package

```bash
go get github.com/getzep/zep/integrations/adk/go@latest
```

The package requires Go 1.25 or later, ADK v1.4.0, and the Zep Go SDK v4.0.0.
The module path for the Zep SDK is `github.com/getzep/zep-go/v4`.

To run the example from a repository checkout:

```bash
cd integrations/adk/go
go mod download
```

## 4. Set environment variables

```bash
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

## 5. Run the example

```bash
go run ./examples
```

The example creates a Zep user and thread before the first turn. Zep returns
the UUIDs. The example passes these UUIDs to the callbacks, search tool, and
memory service.

## 6. Run tests

Run offline unit tests:

```bash
go test ./...
```

Run the live tests with `ZEP_API_KEY` set:

```bash
ZEP_API_KEY=... go test -run TestLive -v ./...
```

The live tests create and delete UUID-addressed Zep resources.

## Troubleshooting

- If authentication fails, check that `ZEP_API_KEY` belongs to the intended
  project.
- If the example has no memory, check that `ZEP_API_KEY` is exported in the
  same shell.
- Zep processes messages asynchronously. Search results can take time to
  include new messages.
