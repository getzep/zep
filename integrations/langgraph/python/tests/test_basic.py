"""
Basic structure and public-API tests for the zep-langgraph package.
"""

from unittest.mock import MagicMock

from langgraph.store.base import BaseStore


def test_package_import() -> None:
    """The package imports successfully."""
    import zep_langgraph

    assert zep_langgraph is not None


def test_public_exports() -> None:
    """The expected public API is exported."""
    from zep_langgraph import (
        ZepStore,
        build_system_message,
        create_graph_search_tool,
        get_zep_context,
        persist_messages,
    )

    assert build_system_message is not None
    assert get_zep_context is not None
    assert persist_messages is not None
    assert create_graph_search_tool is not None
    assert ZepStore is not None


class TestPackageStructure:
    """Metadata attributes exist."""

    def test_version_exists(self) -> None:
        import zep_langgraph

        assert hasattr(zep_langgraph, "__version__")
        assert zep_langgraph.__version__ == "0.1.0"

    def test_author_exists(self) -> None:
        import zep_langgraph

        assert hasattr(zep_langgraph, "__author__")

    def test_description_exists(self) -> None:
        import zep_langgraph

        assert hasattr(zep_langgraph, "__description__")

    def test_all_is_complete(self) -> None:
        import zep_langgraph

        for name in zep_langgraph.__all__:
            assert hasattr(zep_langgraph, name), f"{name} in __all__ but not importable"


class TestZepStoreIsBaseStore:
    """ZepStore must be a fully-concrete BaseStore.

    These are the contract assertions called out in the build spec: the adapter
    instantiates, is a BaseStore, and implements every abstract method (only
    ``batch`` / ``abatch`` are abstract on the base class).
    """

    def _make_store(self) -> "ZepStore":  # type: ignore[name-defined]  # noqa: F821
        from zep_cloud.client import AsyncZep

        from zep_langgraph import ZepStore

        return ZepStore(MagicMock(spec=AsyncZep))

    def test_is_instance_of_base_store(self) -> None:
        store = self._make_store()
        assert isinstance(store, BaseStore)

    def test_no_remaining_abstract_methods(self) -> None:
        from zep_langgraph import ZepStore

        assert ZepStore.__abstractmethods__ == frozenset()

    def test_instantiates_without_error(self) -> None:
        store = self._make_store()
        assert store is not None
