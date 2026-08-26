"""ConcatLoader: several loaders as one episode stream into one graph."""

from collections.abc import Iterator, Sequence

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.protocols import Loader
from zep_ingest.types import Episode


class ConcatLoader:
    """Yield episodes from each loader in order as a single stream.

    Use this when several sources (JSON records, documents, emails, …) belong
    on the same graph: one ``Pipeline.run`` / ``ingest(...)`` submits everything
    together. Sequential vs batch only chooses the submit API; neither waits
    for one file to finish processing before the next is sent.
    """

    def __init__(self, loaders: Sequence[Loader]) -> None:
        self.loaders = list(loaders)
        if not self.loaders:
            raise ConfigurationError("ConcatLoader requires at least one loader")

    def load(self) -> Iterator[Episode]:
        for loader in self.loaders:
            yield from loader.load()

    def flush_warnings(self) -> None:
        for loader in self.loaders:
            flush = getattr(loader, "flush_warnings", None)
            if callable(flush):
                flush()

    @property
    def warnings(self) -> list[str]:
        collected: list[str] = []
        for loader in self.loaders:
            collected.extend(getattr(loader, "warnings", []))
        return collected
