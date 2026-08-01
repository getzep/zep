"""Basic structure / import tests for the zep-strands package."""

from unittest.mock import MagicMock

import pytest


def test_package_import() -> None:
    import zep_strands

    assert zep_strands is not None


def test_public_exports() -> None:
    from zep_strands import (
        ZepMemoryStore,
        create_zep_search_tool,
        ensure_thread,
        ensure_user,
    )

    assert ZepMemoryStore is not None
    assert create_zep_search_tool is not None
    assert ensure_user is not None
    assert ensure_thread is not None


class TestPackageMetadata:
    def test_version_exists(self) -> None:
        import zep_strands

        assert zep_strands.__version__ == "0.1.0"

    def test_author_and_description(self) -> None:
        import zep_strands

        assert hasattr(zep_strands, "__author__")
        assert hasattr(zep_strands, "__description__")


class TestZepMemoryStoreInit:
    def test_construct_user_mode(self) -> None:
        from zep_strands import ZepMemoryStore

        store = ZepMemoryStore(
            zep_client=MagicMock(),
            user_id="u1",
            thread_id="t1",
            first_name="Jane",
            last_name="Smith",
        )
        assert store.name == "zep"
        assert store.writable is True
        assert store.extraction is True
        assert store.user_message_name == "Jane Smith"
        assert store.search_scope == "auto"

    def test_construct_graph_mode(self) -> None:
        from zep_strands import ZepMemoryStore

        store = ZepMemoryStore(
            zep_client=MagicMock(),
            graph_id="g1",
            writable=True,
        )
        assert store.graph_id == "g1"
        # No thread → no default server-side extraction.
        assert store.extraction is None

    def test_rejects_neither_scope(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="user_id or graph_id"):
            ZepMemoryStore(zep_client=MagicMock())

    def test_rejects_both_scopes(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="only one"):
            ZepMemoryStore(zep_client=MagicMock(), user_id="u", graph_id="g")

    def test_rejects_empty_name(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="name"):
            ZepMemoryStore(zep_client=MagicMock(), user_id="u", name="  ")

    def test_rejects_invalid_max_search_results(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="max_search_results"):
            ZepMemoryStore(zep_client=MagicMock(), user_id="u", max_search_results=0)

    def test_rejects_extraction_without_thread(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="user_id and thread_id"):
            ZepMemoryStore(
                zep_client=MagicMock(),
                user_id="u1",
                thread_id=None,
                extraction=True,
            )

    def test_rejects_extraction_on_standalone_graph(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="user_id and thread_id"):
            ZepMemoryStore(
                zep_client=MagicMock(),
                graph_id="g1",
                writable=True,
                extraction=True,
            )

    def test_rejects_extraction_when_not_writable(self) -> None:
        from zep_strands import ZepMemoryStore

        with pytest.raises(ValueError, match="writable=True"):
            ZepMemoryStore(
                zep_client=MagicMock(),
                user_id="u1",
                thread_id="t1",
                writable=False,
                extraction=True,
            )

    def test_allows_explicit_extraction_false_without_thread(self) -> None:
        from zep_strands import ZepMemoryStore

        store = ZepMemoryStore(
            zep_client=MagicMock(),
            graph_id="g1",
            writable=True,
            extraction=False,
        )
        assert store.extraction is False
