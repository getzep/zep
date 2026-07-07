"""Structural protocols — the package's extension surface.

New source = Loader, new preparation step = Transform, new submission path =
Submitter, any LLM provider = LLMClient. All are structural (no base classes
to inherit); transforms are stream-shaped so one protocol covers 1→1
(formatting), 1→many (chunking), and many→1 (grouping) while keeping the whole
pipeline lazy.

Loaders and transforms may optionally expose a ``warnings: list[str]``
attribute; Pipeline.run collects it into IngestResult.warnings. A transform
that accumulates statistics mid-stream may also expose ``flush_warnings()``,
which Pipeline calls before collecting — a limited preview() can leave the
episode generator suspended, so warnings must not depend on stream exhaustion.
"""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from zep_ingest.types import Destination, Episode

if TYPE_CHECKING:
    from zep_ingest.result import IngestResult


@runtime_checkable
class Loader(Protocol):
    def load(self) -> Iterator[Episode]: ...


@runtime_checkable
class Transform(Protocol):
    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]: ...


@runtime_checkable
class Submitter(Protocol):
    def submit(self, episodes: Iterable[Episode], destination: Destination) -> "IngestResult": ...


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...
