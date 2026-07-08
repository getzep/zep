"""
Tests for ``context_template`` on ``ZepMemoryManager`` -- the override
mechanism and the plain ``str.replace`` rendering contract (never
``str.format``, so a context string or a custom template containing ``{``,
``}``, or ``%`` is always safe to inject).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager
from zep_ag2.memory import DEFAULT_CONTEXT_TEMPLATE


def _make_mock_client(context: str = "Alice likes hiking") -> MagicMock:
    client = MagicMock(spec=AsyncZep)
    client.thread = MagicMock()
    client.thread.get_user_context = AsyncMock(return_value=MagicMock(context=context))
    client.thread.add_messages = AsyncMock(return_value=MagicMock(context=context))
    return client


class TestDefaultTemplate:
    def test_default_template_is_canonical(self) -> None:
        assert "{context}" in DEFAULT_CONTEXT_TEMPLATE
        assert "<ZEP_CONTEXT>" in DEFAULT_CONTEXT_TEMPLATE
        assert "</ZEP_CONTEXT>" in DEFAULT_CONTEXT_TEMPLATE


class TestContextTemplateOverride:
    @pytest.mark.asyncio
    async def test_context_template_override(self) -> None:
        client = _make_mock_client()
        custom_template = "CUSTOM START\n{context}\nCUSTOM END"
        manager = ZepMemoryManager(
            client, user_id="u1", session_id="s1", context_template=custom_template
        )
        agent = MagicMock()
        agent.system_message = "base"
        agent.update_system_message = MagicMock()

        await manager.enrich_system_message(agent)

        injected = agent.update_system_message.call_args[0][0]
        assert "CUSTOM START" in injected
        assert "CUSTOM END" in injected
        assert "Alice likes hiking" in injected

    @pytest.mark.asyncio
    async def test_template_rendered_via_replace_not_format(self) -> None:
        """A context string containing '{' / '}' / '%' must not raise or be
        mangled -- proves rendering uses str.replace, not str.format."""
        client = _make_mock_client(context="Weird {braces} and %s percent stuff")
        manager = ZepMemoryManager(client, user_id="u1", session_id="s1")
        agent = MagicMock()
        agent.system_message = "base"
        agent.update_system_message = MagicMock()

        await manager.enrich_system_message(agent)

        injected = agent.update_system_message.call_args[0][0]
        assert "Weird {braces} and %s percent stuff" in injected

    @pytest.mark.asyncio
    async def test_custom_template_with_braces_survives_replace(self) -> None:
        """A custom template containing extra literal braces beyond the
        {context} placeholder must survive str.replace rendering."""
        client = _make_mock_client(context="fact")
        custom_template = "{{not_a_placeholder}} {context} {{also_not}}"
        manager = ZepMemoryManager(
            client, user_id="u1", session_id="s1", context_template=custom_template
        )
        agent = MagicMock()
        agent.system_message = "base"
        agent.update_system_message = MagicMock()

        await manager.enrich_system_message(agent)

        injected = agent.update_system_message.call_args[0][0]
        assert "{{not_a_placeholder}} Memory context: fact {{also_not}}" in injected
