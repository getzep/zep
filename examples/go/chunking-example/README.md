# Document Chunking with Contextualized Retrieval for Zep

This example demonstrates how to implement Anthropic's **contextualized retrieval** technique with Zep. The script chunks a document, uses OpenAI to generate contextual descriptions for each chunk, and ingests the contextualized chunks into Zep's knowledge graph.

## Why Contextualized Retrieval?

Traditional RAG systems chunk documents and embed them directly. This loses important context because each chunk is processed in isolation. For example, a chunk mentioning "the policy" without specifying which policy becomes ambiguous.

Contextualized retrieval solves this by prepending a brief context to each chunk that situates it within the full document. This improves retrieval accuracy by helping the embedding model understand what each chunk is actually about.

**Example:**

Before contextualization:
```
Employees may carry over up to 5 unused PTO days to the following year.
```

After contextualization:
```
This chunk describes ACME Corporation's PTO carryover policy from the
Employee Handbook effective January 1, 2024. It appears in the Time Off
and Leave Policies section.

---

Employees may carry over up to 5 unused PTO days to the following year.
```

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure environment variables in `.env`:
   ```
   ZEP_API_KEY=your_zep_api_key
   OPENAI_API_KEY=your_openai_api_key
   ```

## Usage

### Basic Usage

Process a document and ingest it into Zep:

```bash
python chunk_and_ingest.py sample_document.txt --user-id user123
```

### Custom Chunk Size

Adjust the chunk size (default is 6000 characters):

```bash
python chunk_and_ingest.py sample_document.txt --user-id user123 --chunk-size 4000
```

### Dry Run

Test the chunking and contextualization without ingesting to Zep:

```bash
python chunk_and_ingest.py sample_document.txt --user-id user123 --dry-run
```

### Wait for Processing

Wait for each episode to be processed before continuing:

```bash
python chunk_and_ingest.py sample_document.txt --user-id user123 --wait
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `document` | Path to the document to process | (required) |
| `--user-id` | Zep user ID for the knowledge graph | (required) |
| `--chunk-size` | Maximum characters per chunk | 6000 |
| `--chunk-overlap` | Character overlap between chunks | 200 |
| `--wait` | Wait for processing after each chunk | False |
| `--dry-run` | Process without ingesting to Zep | False |

## How It Works

1. **Document Chunking**: The document is split into chunks using a paragraph-first strategy:
   - Split by double newlines (paragraphs)
   - If a paragraph exceeds the chunk size, split by sentences
   - Maintain configurable overlap between chunks

2. **Contextualization**: Each chunk is sent to OpenAI's gpt-4o-mini with the full document context. The model generates a brief description situating the chunk within the document.

3. **Ingestion**: The contextualized chunk (context + separator + original chunk) is ingested into Zep using `client.graph.add()`.

## Example Output

```
============================================================
DOCUMENT CHUNKING WITH CONTEXTUALIZED RETRIEVAL
============================================================
Document: sample_document.txt
User ID: user123
Chunk size: 6000
Chunk overlap: 200
Dry run: False

Reading document: sample_document.txt
Document size: 15,432 characters

Chunking document (chunk_size=6000, overlap=200)...
Created 4 chunks

Processing chunks:
------------------------------------------------------------

Chunk 1/4 (5,842 chars)
  Contextualizing with OpenAI...
  Context: "This chunk covers the introduction and company values..."
  Ingesting to Zep...
  Created episode: ep_abc123...

...

============================================================
PROCESSING SUMMARY
============================================================
Total chunks: 4
Successfully processed: 4
Failed: 0
Original document size: 15,432 characters
Total contextualized size: 16,890 characters
Size expansion from contextualization: 9.4%
============================================================
```

## Notes

- **Chunk Size**: The default 6000 characters leaves room for the context prefix while staying within Zep's 10K character episode limit.

- **Rate Limits**: The script includes retry logic with exponential backoff for OpenAI rate limits.

- **Error Handling**: Failed chunks are tracked and reported in the summary. The script continues processing remaining chunks after failures.

## Sample Document

The included `sample_document.txt` is a fictional company employee handbook (~3000 words) covering:
- Remote work policies
- Time off and leave
- Professional development
- Performance management
- Workplace conduct
- Information security
- Benefits

This provides a realistic test document with structured content that benefits from contextualization.
