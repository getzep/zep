def get_source_description(chunk: dict) -> str:
    """Return the source description for a document chunk.
    Customize this per use case (e.g., map to URLs, include text fragments).

    The chunk dict contains: filename, title, summary, chunk_index,
    total_chunks, content, context.

    Default: returns the filename as-is.
    """
    return chunk["filename"]
