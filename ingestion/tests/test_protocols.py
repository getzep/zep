"""Tests for the structural protocols that define the extension surface."""

from collections.abc import Iterable, Iterator

from zep_ingest.protocols import LLMClient, Loader, Submitter, Transform
from zep_ingest.result import IngestResult
from zep_ingest.types import Destination, Episode


class _FakeLoader:
    def load(self) -> Iterator[Episode]:
        yield Episode(data="x")


class _FakeTransform:
    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        yield from episodes


class _FakeSubmitter:
    def submit(self, episodes: Iterable[Episode], destination: Destination) -> IngestResult:
        return IngestResult(method="sequential")


class _FakeLLM:
    def complete(self, prompt: str) -> str:
        return "context"


def test_structural_conformance():
    assert isinstance(_FakeLoader(), Loader)
    assert isinstance(_FakeTransform(), Transform)
    assert isinstance(_FakeSubmitter(), Submitter)
    assert isinstance(_FakeLLM(), LLMClient)


def test_non_conforming_objects_rejected():
    class Nothing:
        pass

    assert not isinstance(Nothing(), Loader)
    assert not isinstance(Nothing(), Transform)
    assert not isinstance(Nothing(), Submitter)
    assert not isinstance(Nothing(), LLMClient)
