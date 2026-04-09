def preprocess_document(content: str, filename: str) -> str:
    """
    Pre-process a document before chunking.

    Called after loading raw file content and before any chunking or
    LLM summarization. Override this function per use case to strip
    noise, normalize formatting, remove boilerplate, etc.

    Args:
        content: Raw document text.
        filename: Name of the source file (e.g., "guide.md").

    Returns:
        The (possibly transformed) document text.
    """
    return content
