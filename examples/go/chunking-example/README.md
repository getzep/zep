# Document Chunking with Contextualized Retrieval for Zep

This example demonstrates Anthropic's **contextualized retrieval** technique with Zep. The program chunks a document, uses OpenAI to generate contextual descriptions for each chunk, and ingests the contextualized chunks into Zep's knowledge graph via `github.com/getzep/zep-go/v4`.

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

Create a user with the root Go example and copy its printed Zep user UUID:

```bash
cd ../
go run .
```

Run the chunking example with that returned UUID:

```bash
cd chunking-example
go run . sample_document.txt --user-uuid <zep-user-uuid>
```

### Custom chunk size

```bash
go run . sample_document.txt --user-uuid <zep-user-uuid> --chunk-size 4000
```

### Dry run

Process chunking/contextualization without ingesting to Zep (still requires `OPENAI_API_KEY`):

```bash
go run . sample_document.txt --user-uuid <zep-user-uuid> --dry-run
```

### Wait for processing

Submit all chunks, then wait once for the final submitted task:

```bash
go run . sample_document.txt --user-uuid <zep-user-uuid> --wait
```

### Help

```bash
go run . --help
```

## Command line options

| Option | Description | Default |
|--------|-------------|---------|
| `document` | Path to the document to process | (required) |
| `--user-uuid` | Zep-returned user UUID for the knowledge graph | (required) |
| `--chunk-size` | Maximum characters per chunk | 6000 |
| `--chunk-overlap` | Character overlap between chunks | 200 |
| `--wait` | Wait once for the final submitted task | false |
| `--dry-run` | Process without ingesting to Zep | false |

## How it works

1. **Document chunking**: Split by paragraphs, then sentences when needed, with configurable overlap.
2. **Contextualization**: Each chunk is sent to OpenAI (`gpt-5-mini`) with the full document; the model returns a short situating context.
3. **Ingestion**: Contextualized chunks are added with `client.Graph.Episode.Add` (`type=text`) and grouped by a content-based document ID.
4. **`--wait`**: The program submits every chunk before it polls the final task. Polling stops on success, a terminal task error, or timeout.

## Notes

- Default chunk size 6000 leaves room for the context prefix under Zep's 10K episode limit.
- Retry with exponential backoff for OpenAI rate limits and transient Zep ingestion errors.
- Failed chunks are counted in the summary; remaining chunks continue processing.
- Live ingestion and `--wait` require `ZEP_API_KEY` and `OPENAI_API_KEY`. `--dry-run` skips Zep but still needs `OPENAI_API_KEY`.

## Sample document

`sample_document.txt` is a fictional employee handbook (~3000 words) covering remote work, time off, development, performance, conduct, security, and benefits.
