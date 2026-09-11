# Document Chunking with Contextualized Retrieval for Zep

This example demonstrates Anthropic's **contextualized retrieval** technique with Zep. The program chunks a document, uses OpenAI to generate contextual descriptions for each chunk, and ingests the contextualized chunks into Zep's knowledge graph via `github.com/getzep/zep-go/v3`.

## Why Contextualized Retrieval?

Traditional RAG systems chunk documents and embed them directly. This loses important context because each chunk is processed in isolation. Contextualized retrieval prepends a brief context to each chunk that situates it within the full document, improving retrieval accuracy.

## Setup

1. Install dependencies:

   ```bash
   cd examples/go/chunking-example
   go mod download
   ```

2. Configure environment variables in `.env` (see `.env.example`):

   ```
   ZEP_API_KEY=your_zep_api_key
   OPENAI_API_KEY=your_openai_api_key
   ```

## Usage

### Basic usage

```bash
go run . sample_document.txt --user-id user123
```

### Custom chunk size

```bash
go run . sample_document.txt --user-id user123 --chunk-size 4000
```

### Dry run

Process chunking/contextualization without ingesting to Zep (still requires `OPENAI_API_KEY`):

```bash
go run . sample_document.txt --user-id user123 --dry-run
```

### Wait for processing

Poll each created episode until `processed` is true (or a linked task fails / timeout):

```bash
go run . sample_document.txt --user-id user123 --wait
```

### Help

```bash
go run . --help
```

## Command line options

| Option | Description | Default |
|--------|-------------|---------|
| `document` | Path to the document to process | (required) |
| `--user-id` | Zep user ID for the knowledge graph | (required) |
| `--chunk-size` | Maximum characters per chunk | 6000 |
| `--chunk-overlap` | Character overlap between chunks | 200 |
| `--wait` | Wait for processing after each chunk | false |
| `--dry-run` | Process without ingesting to Zep | false |

## How it works

1. **Document chunking**: Split by paragraphs, then sentences when needed, with configurable overlap.
2. **Contextualization**: Each chunk is sent to OpenAI (`gpt-4o-mini`) with the full document; the model returns a short situating context.
3. **Ingestion**: Contextualized chunks are added with `client.Graph.Add` (`type=text`).
4. **`--wait`**: Bounded polling of `Graph.Episode.Get` using `episode.processed`, with fail-fast on linked task statuses `failed` / `error` / `canceled` / `cancelled` / `partial`.

## Notes

- Default chunk size 6000 leaves room for the context prefix under Zep's 10K episode limit.
- Retry with exponential backoff for OpenAI rate limits and transient Zep ingestion errors.
- Failed chunks are counted in the summary; remaining chunks continue processing.
- Live ingestion and `--wait` require `ZEP_API_KEY` and `OPENAI_API_KEY`. `--dry-run` skips Zep but still needs `OPENAI_API_KEY`.
- Unit tests cover CLI parsing and wait/status handling without live API keys: `go test ./...`.

## Sample document

`sample_document.txt` is a fictional employee handbook (~3000 words) covering remote work, time off, development, performance, conduct, security, and benefits.
