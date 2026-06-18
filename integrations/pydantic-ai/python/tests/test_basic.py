"""
Basic structure / import tests for the zep-pydantic-ai package.
"""

from unittest.mock import MagicMock


def test_package_import() -> None:
    """The package imports successfully."""
    import zep_pydantic_ai

    assert zep_pydantic_ai is not None


def test_public_exports() -> None:
    """The expected public API is exported."""
    from zep_pydantic_ai import (
        ZepDeps,
        create_zep_search_tool,
        persist_run,
        zep_history_processor,
    )

    assert ZepDeps is not None
    assert zep_history_processor is not None
    assert persist_run is not None
    assert create_zep_search_tool is not None


class TestPackageMetadata:
    def test_version_exists(self) -> None:
        import zep_pydantic_ai

        assert hasattr(zep_pydantic_ai, "__version__")
        assert zep_pydantic_ai.__version__ == "0.1.0"

    def test_author_and_description(self) -> None:
        import zep_pydantic_ai

        assert hasattr(zep_pydantic_ai, "__author__")
        assert hasattr(zep_pydantic_ai, "__description__")


class TestZepDeps:
    def test_construct_minimal(self) -> None:
        from zep_pydantic_ai import ZepDeps

        deps = ZepDeps(client=MagicMock(), user_id="u", thread_id="t")
        assert deps.user_id == "u"
        assert deps.thread_id == "t"
        assert deps.assistant_name == "Assistant"
        assert deps.ignore_roles is None

    def test_display_name_from_first_last(self) -> None:
        from zep_pydantic_ai import ZepDeps

        deps = ZepDeps(
            client=MagicMock(),
            user_id="u",
            thread_id="t",
            first_name="Jane",
            last_name="Smith",
        )
        assert deps.display_name == "Jane Smith"

    def test_display_name_explicit_user_name_wins(self) -> None:
        from zep_pydantic_ai import ZepDeps

        deps = ZepDeps(
            client=MagicMock(),
            user_id="u",
            thread_id="t",
            first_name="Jane",
            last_name="Smith",
            user_name="JaneS",
        )
        assert deps.display_name == "JaneS"

    def test_display_name_none_when_no_names(self) -> None:
        from zep_pydantic_ai import ZepDeps

        deps = ZepDeps(client=MagicMock(), user_id="u", thread_id="t")
        assert deps.display_name is None

    def test_display_name_first_only(self) -> None:
        from zep_pydantic_ai import ZepDeps

        deps = ZepDeps(client=MagicMock(), user_id="u", thread_id="t", first_name="Jane")
        assert deps.display_name == "Jane"
