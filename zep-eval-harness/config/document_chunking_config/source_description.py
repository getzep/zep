def get_source_description(filename: str) -> str:
    """Return the source description for a document chunk.
    Customize this per use case (e.g., map filenames to URLs).
    Default: returns the filename as-is.
    """
    return filename
