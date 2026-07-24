"""Pipeline: Loader → Transforms → LimitGuard → Submitter, plus one-liners.

preview() runs a lazy sample through the full chain by default, with no Zep API
calls. Its warning scope is explicit; pass ``limit=None`` to validate the full
stream for missing timestamps, oversize splits, and runaway alias rewrites.
run() adds the preflights that encode Zep's order-of-operations rules: ontology
before ingestion, destination existence before submission.
"""

import pickle
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, Literal

from zep_cloud.client import Zep
from zep_cloud.errors.not_found_error import NotFoundError

from zep_ingest._validation import require_nonnegative_number
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.email import EmlLoader
from zep_ingest.loaders.json_records import JsonRecordsLoader
from zep_ingest.loaders.slack import DEFAULT_SKIP_SUBTYPES, SlackExportLoader
from zep_ingest.loaders.text import TextFileLoader
from zep_ingest.loaders.transcript import DEFAULT_CHUNK_CHARS, TranscriptLoader
from zep_ingest.protocols import LLMClient, Loader, Submitter, Transform
from zep_ingest.result import IngestResult
from zep_ingest.submitters import Method, submit_episodes
from zep_ingest.transforms.canonicalizer import DEFAULT_RISKY_WORDS, AliasCanonicalizer
from zep_ingest.transforms.chunker import TextChunker
from zep_ingest.transforms.json_normalizer import JsonNormalizer
from zep_ingest.transforms.limits import LimitGuard
from zep_ingest.types import Destination, Episode

#: entities/edges in the same shapes client.graph.set_ontology accepts.
OntologySpec = dict[str, Any]


@dataclass
class PreviewReport:
    episodes: list[Episode]
    warnings: list[str] = field(default_factory=list)


class _MissingTimestampCounter:
    def __init__(self) -> None:
        self.count = 0

    def wrap(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        for episode in episodes:
            if episode.created_at is None:
                self.count += 1
            yield episode

    @property
    def warnings(self) -> list[str]:
        if self.count == 0:
            return []
        return [
            f"{self.count} episode(s) have no created_at timestamp. Zep silently "
            "defaults to the ingestion time, which corrupts fact validity timelines "
            "and invalidation ordering on backfills. Supply original event "
            "timestamps wherever possible."
        ]


@contextmanager
def _validated_replay(episodes: Iterable[Episode]) -> Iterator[Iterator[Episode]]:
    """Fully validate a stream before submission, spilling large runs to disk."""
    with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as spool:
        for episode in episodes:
            record = (
                episode.data,
                episode.data_type,
                episode.created_at,
                episode.metadata,
                episode.source_description,
                episode.document,
            )
            pickle.dump(record, spool, protocol=pickle.HIGHEST_PROTOCOL)
        spool.seek(0)

        def replay() -> Iterator[Episode]:
            while True:
                try:
                    data, data_type, created_at, metadata, source_description, document = (
                        pickle.load(spool)
                    )
                except EOFError:
                    return
                yield Episode(
                    data=data,
                    data_type=data_type,
                    created_at=created_at,
                    metadata=metadata,
                    source_description=source_description,
                    document=document,
                )

        yield replay()


@dataclass
class Pipeline:
    loader: Loader
    transforms: Sequence[Transform] = ()
    submitter: Submitter | None = None

    def _stream(self, guard: LimitGuard, counter: _MissingTimestampCounter) -> Iterator[Episode]:
        episodes: Iterable[Episode] = self.loader.load()
        for transform in self.transforms:
            episodes = transform.apply(episodes)
        return counter.wrap(guard.apply(episodes))

    def _warning_sources(self) -> tuple[Any, ...]:
        return (self.loader, *self.transforms)

    def _warning_baseline(self) -> dict[int, int]:
        """The loader and transforms may be reused across preview() and run();
        collect only the warnings each pass adds, not the accumulated history."""
        return {
            id(source): len(getattr(source, "warnings", [])) for source in self._warning_sources()
        }

    def _collect_warnings(
        self,
        guard: LimitGuard,
        counter: _MissingTimestampCounter,
        baseline: dict[int, int],
    ) -> list[str]:
        warnings: list[str] = []
        for source in self._warning_sources():
            # a limited preview() leaves the stream suspended, so transforms
            # that accumulate stats (e.g. alias counts) flush them here
            flush = getattr(source, "flush_warnings", None)
            if callable(flush):
                flush()
            source_warnings = getattr(source, "warnings", [])
            warnings.extend(source_warnings[baseline.get(id(source), 0) :])
        warnings.extend(guard.warnings)
        warnings.extend(counter.warnings)
        return warnings

    def preview(self, limit: int | None = 10) -> PreviewReport:
        """Run the chain with no Zep API calls.

        The default returns a lazy sample and labels warnings as sample-scoped.
        Pass ``limit=None`` for exhaustive validation and warning counts.
        """
        guard = LimitGuard()
        counter = _MissingTimestampCounter()
        baseline = self._warning_baseline()
        stream = self._stream(guard, counter)
        episodes = list(stream) if limit is None else list(islice(stream, limit))
        warnings = self._collect_warnings(guard, counter, baseline)
        if limit is not None:
            warnings.append(
                f"Preview is limited to a sample of at most {limit} transformed episode(s); "
                "warning counts cover only that sample. Later episodes may still be missing "
                "created_at or contain other validation issues. Use preview(limit=None) for "
                "an exhaustive preflight."
            )
        return PreviewReport(episodes=episodes, warnings=warnings)

    def run(
        self,
        client: Zep,
        *,
        graph_id: str | None = None,
        user_id: str | None = None,
        method: Method = "auto",
        ontology: OntologySpec | None = None,
        create_if_missing: bool = False,
        batch_metadata: dict[str, Any] | None = None,
        wait: bool = False,
        poll_interval: float = 10.0,
        timeout: float | None = None,
    ) -> IngestResult:
        require_nonnegative_number("poll_interval", poll_interval)
        if timeout is not None:
            require_nonnegative_number("timeout", timeout)
        destination = Destination(graph_id=graph_id, user_id=user_id)
        if self.submitter is not None and (method != "auto" or batch_metadata is not None):
            raise ConfigurationError(
                "method and batch_metadata cannot be used with a custom Pipeline submitter"
            )
        guard = LimitGuard()
        counter = _MissingTimestampCounter()
        baseline = self._warning_baseline()
        with _validated_replay(self._stream(guard, counter)) as stream:
            if create_if_missing:
                _ensure_destination(client, destination)
            if ontology is not None:
                _apply_ontology(client, destination, ontology)
            if self.submitter is not None:
                result = self.submitter.submit(stream, destination)
            else:
                result = submit_episodes(
                    client,
                    stream,
                    destination,
                    method=method,
                    batch_metadata=batch_metadata,
                )
        result.warnings.extend(self._collect_warnings(guard, counter, baseline))
        if wait:
            result.wait(poll_interval=poll_interval, timeout=timeout)
        return result


def _ensure_destination(client: Zep, destination: Destination) -> None:
    if destination.graph_id is not None:
        try:
            client.graph.get(destination.graph_id)
        except NotFoundError:
            client.graph.create(graph_id=destination.graph_id)
    else:
        user_id = destination.user_id or ""
        try:
            client.user.get(user_id)
        except NotFoundError:
            client.user.add(user_id=user_id)


def _apply_ontology(client: Zep, destination: Destination, ontology: OntologySpec) -> None:
    if not isinstance(ontology, dict) or "entities" not in ontology:
        raise ConfigurationError(
            'ontology must be a dict with an "entities" key (and optionally "edges"), '
            "matching client.graph.set_ontology(entities=..., edges=...)."
        )
    scope: dict[str, list[str]] = {}
    if destination.graph_id is not None:
        scope["graph_ids"] = [destination.graph_id]
    else:
        scope["user_ids"] = [destination.user_id or ""]
    # set_ontology lives on the SDK's external graph client wrapper, which the
    # type stubs don't surface on GraphClient
    client.graph.set_ontology(  # type: ignore[attr-defined]
        entities=ontology["entities"], edges=ontology.get("edges"), **scope
    )


def ingest(
    client: Zep,
    loader: Loader,
    *,
    transforms: Sequence[Transform] = (),
    **run_kwargs: Any,
) -> IngestResult:
    """Run a Loader (and optional Transforms) through the standard pipeline."""
    return Pipeline(loader, transforms=transforms).run(client, **run_kwargs)


def _alias_transforms(
    aliases: dict[str, Sequence[str]] | None,
    risky_words: frozenset[str] | None,
) -> list[Transform]:
    """The one place the alias canonicalizer is built for the one-liners."""
    if not aliases:
        return []
    guard = DEFAULT_RISKY_WORDS if risky_words is None else risky_words
    return [AliasCanonicalizer(aliases, risky_words=guard)]


def ingest_slack_export(
    client: Zep,
    path: str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    channels: Sequence[str] | None = None,
    grouping: Literal["thread", "message"] = "thread",
    include_bots: bool = False,
    skip_subtypes: frozenset[str] = DEFAULT_SKIP_SUBTYPES,
    formatter: Callable[..., str] | None = None,
    aliases: dict[str, Sequence[str]] | None = None,
    risky_words: frozenset[str] | None = None,
    **run_kwargs: Any,
) -> IngestResult:
    """One-liner: Slack workspace export (.zip or directory) → Zep graph."""
    loader = SlackExportLoader(
        path,
        channels=channels,
        grouping=grouping,
        include_bots=include_bots,
        skip_subtypes=skip_subtypes,
        formatter=formatter,
    )
    transforms = _alias_transforms(aliases, risky_words)
    return Pipeline(loader, transforms=transforms).run(
        client, graph_id=graph_id, user_id=user_id, **run_kwargs
    )


def ingest_documents(
    client: Zep,
    path_or_glob: str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    llm: LLMClient | None = None,
    chunk_size: int = 500,
    overlap: int = 50,
    created_at: str | None = None,
    use_file_mtime: bool = False,
    aliases: dict[str, Sequence[str]] | None = None,
    risky_words: frozenset[str] | None = None,
    **run_kwargs: Any,
) -> IngestResult:
    """One-liner: text/Markdown files → chunked (and optionally LLM-contextualized)
    text episodes."""
    loader = TextFileLoader(path_or_glob, created_at=created_at, use_file_mtime=use_file_mtime)
    transforms = _alias_transforms(aliases, risky_words)
    transforms.append(TextChunker(chunk_size=chunk_size, overlap=overlap))
    if llm is not None:
        from zep_ingest.transforms.contextualizer import LLMContextualizer

        transforms.append(LLMContextualizer(llm))
    return Pipeline(loader, transforms=transforms).run(
        client, graph_id=graph_id, user_id=user_id, **run_kwargs
    )


def ingest_transcripts(
    client: Zep,
    path_or_glob: str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    meeting_start: str | None = None,
    default_start_time: str | None = None,
    aliases: dict[str, Sequence[str]] | None = None,
    risky_words: frozenset[str] | None = None,
    **run_kwargs: Any,
) -> IngestResult:
    """Ingest speaker-labeled or WebVTT transcripts at turn boundaries."""
    loader = TranscriptLoader(
        path_or_glob,
        chunk_chars=chunk_chars,
        meeting_start=meeting_start,
        default_start_time=default_start_time,
    )
    return Pipeline(loader, transforms=_alias_transforms(aliases, risky_words)).run(
        client, graph_id=graph_id, user_id=user_id, **run_kwargs
    )


def ingest_emails(
    client: Zep,
    path_or_glob: str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    aliases: dict[str, Sequence[str]] | None = None,
    risky_words: frozenset[str] | None = None,
    **run_kwargs: Any,
) -> IngestResult:
    """One-liner: .eml files → text episodes dated by their Date headers."""
    transforms = _alias_transforms(aliases, risky_words)
    return Pipeline(EmlLoader(path_or_glob), transforms=transforms).run(
        client, graph_id=graph_id, user_id=user_id, **run_kwargs
    )


def ingest_json_records(
    client: Zep,
    path_or_glob: str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    format: Literal["auto", "jsonl", "csv", "json"] = "auto",
    id_field: str | None = None,
    name_field: str | None = None,
    description_field: str | None = None,
    created_at_field: str | None = None,
    metadata_fields: Sequence[str] = (),
    record_type: str | None = None,
    **run_kwargs: Any,
) -> IngestResult:
    """One-liner: structured records (JSONL/CSV/JSON array) → normalized json episodes."""
    loader = JsonRecordsLoader(
        path_or_glob,
        format=format,
        id_field=id_field,
        name_field=name_field,
        description_field=description_field,
        created_at_field=created_at_field,
        metadata_fields=metadata_fields,
        record_type=record_type,
    )
    return Pipeline(loader, transforms=(JsonNormalizer(),)).run(
        client, graph_id=graph_id, user_id=user_id, **run_kwargs
    )
