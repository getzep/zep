"""
Tests for ``context_template`` on the storage adapters: overriding the
template used to wrap composed/built context, and the ``str.replace`` (never
``str.format``) rendering contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from zep_cloud.client import Zep

from zep_crewai import DEFAULT_CONTEXT_TEMPLATE, ZepUserStorage
from zep_crewai.user_storage import ContextInput


def _make_mock_client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.user = MagicMock()
    client.thread = MagicMock()
    client.graph = MagicMock()
    return client


class TestContextTemplate:
    def test_context_template_override(self) -> None:
        """A custom context_template wraps the built context."""
        client = _make_mock_client()
        custom_template = "CUSTOM WRAP >>> {context} <<< END"

        def builder(ctx: ContextInput) -> str | None:
            return "the retrieved facts"

        storage = ZepUserStorage(
            client=client,
            user_id="user-1",
            thread_id="thread-1",
            context_builder=builder,
            context_template=custom_template,
        )

        results = storage.search("hi")

        assert len(results) == 1
        assert results[0]["context"] == "CUSTOM WRAP >>> the retrieved facts <<< END"

    def test_template_rendered_via_replace_not_format(self) -> None:
        """Context containing literal `{` / `%` must survive unescaped -- this
        would raise or corrupt output under str.format()."""
        tricky_context = "50% done; use {braces} and {other} freely"
        client = _make_mock_client()

        def builder(ctx: ContextInput) -> str | None:
            return tricky_context

        storage = ZepUserStorage(
            client=client, user_id="user-1", thread_id="thread-1", context_builder=builder
        )

        # Must not raise.
        results = storage.search("hi")

        assert len(results) == 1
        assert tricky_context in results[0]["context"]

    def test_default_template_is_canonical(self) -> None:
        assert DEFAULT_CONTEXT_TEMPLATE == (
            "The following context is retrieved from Zep, the agent's long-term memory. "
            "It contains relevant facts, entities, and prior knowledge about the user. "
            "Use it to inform your responses.\n\n"
            "<ZEP_CONTEXT>\n"
            "{context}\n"
            "</ZEP_CONTEXT>"
        )

    def test_default_composition_wrapped_in_template(self) -> None:
        """The default (non-builder) search path also wraps the composed
        context in context_template, via search_graph_and_compose_context."""
        from unittest.mock import patch

        client = _make_mock_client()
        custom_template = "WRAP[{context}]"

        storage = ZepUserStorage(
            client=client,
            user_id="user-1",
            thread_id="thread-1",
            context_template=custom_template,
        )

        with patch(
            "zep_crewai.user_storage.search_graph_and_compose_context",
            return_value="WRAP[composed facts]",
        ) as mock_compose:
            results = storage.search("hi", limit=5)

        assert mock_compose.call_args.kwargs["context_template"] == custom_template
        assert results[0]["context"] == "WRAP[composed facts]"
