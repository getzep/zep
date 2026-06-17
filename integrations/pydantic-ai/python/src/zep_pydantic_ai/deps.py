"""
``ZepDeps`` -- the dependency object that carries the Zep client and identity
through a Pydantic AI run.

A Pydantic AI ``Agent`` is parameterised by a *dependency type* (``deps_type``).
The dependency object is constructed once per run and is available to every
capability, tool, and instruction function via ``RunContext.deps``.  This
integration stores everything Zep needs -- the client, the user, and the thread
-- on a single ``ZepDeps`` dataclass so the history processor and the search
tool can reach it uniformly.

Helpers for translating between Pydantic AI ``ModelMessage`` objects and Zep
``Message`` objects also live here, since both the processor and the
persistence helper need them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from zep_cloud import Message
from zep_cloud.client import AsyncZep

logger = logging.getLogger(__name__)

#: Zep rejects a single message whose content exceeds 4096 characters (HTTP
#: 400).  We truncate a little below that ceiling so the message is always
#: accepted; longer text is clipped before it is sent and a warning is logged.
MAX_MESSAGE_CHARS = 4000


@dataclass
class ZepDeps:
    """Dependencies passed to a Pydantic AI agent that uses Zep memory.

    Construct one ``ZepDeps`` per conversation turn (or reuse it across turns
    for the same user/thread) and pass it to ``agent.run(..., deps=deps)``.
    The history processor and the ``zep_search`` tool both read the client and
    identity from here via ``RunContext.deps``.

    The Zep user and thread are created lazily on first use (see
    :func:`zep_pydantic_ai.history_processor.zep_history_processor`), so you do
    **not** have to pre-create them -- though doing so out-of-band is fine and
    slightly faster on the first turn.

    Attributes:
        client: An initialised ``AsyncZep`` client.  The integration never
            closes this client -- the caller owns its lifecycle.
        user_id: The Zep user ID.  Maps to one user graph; reused across all of
            that user's threads.
        thread_id: The Zep thread ID for the current conversation.
        first_name: Optional user first name.  Passed to ``user.add`` so Zep can
            anchor the user's identity node in the graph.  Strongly recommended.
        last_name: Optional user last name.
        email: Optional user email.  Helps Zep resolve identity.
        user_name: Optional display name attached to persisted *user* messages.
            Defaults to ``"{first_name} {last_name}"`` when names are provided,
            otherwise ``None``.
        assistant_name: Display name attached to persisted *assistant* messages.
            Defaults to ``"Assistant"``.
        ignore_roles: Optional list of message roles to exclude from Zep's
            knowledge-graph ingestion.  Messages are still stored in the thread
            history but are not processed into the user's graph.  Passed through
            to every ``thread.add_messages`` call.
    """

    client: AsyncZep
    user_id: str
    thread_id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    user_name: str | None = None
    assistant_name: str = "Assistant"
    ignore_roles: list[str] | None = None

    #: Internal: tracks whether the user/thread have been created this process,
    #: keyed by ``(user_id, thread_id)``, to avoid redundant create calls.
    _created: set[tuple[str, str]] = field(default_factory=set, repr=False)

    @property
    def display_name(self) -> str | None:
        """The name attached to persisted user messages.

        Uses ``user_name`` if set, otherwise composes first/last name, otherwise
        ``None``.
        """
        if self.user_name:
            return self.user_name
        composed = " ".join(p for p in (self.first_name, self.last_name) if p).strip()
        return composed or None


def truncate_message_content(text: str, *, label: str = "message") -> str:
    """Clip text to Zep's per-message limit, never raising.

    Zep returns HTTP 400 for messages longer than 4096 characters, so overlong
    content must be clipped before it is sent.  Truncation is logged at WARNING
    level with **lengths only** -- never the content itself -- so no message
    text or PII reaches the logs.

    Args:
        text: The candidate message content.
        label: A short, non-PII tag for the log line (e.g. ``"user turn"``).

    Returns:
        ``text`` unchanged if within the limit, otherwise clipped to
        :data:`MAX_MESSAGE_CHARS`.
    """
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    logger.warning(
        "Truncating Zep %s: %d chars exceeds limit of %d; clipped to %d",
        label,
        len(text),
        MAX_MESSAGE_CHARS,
        MAX_MESSAGE_CHARS,
    )
    return text[:MAX_MESSAGE_CHARS]


def latest_user_text(messages: list[ModelMessage]) -> str | None:
    """Return the text of the most recent user prompt in ``messages``.

    Pydantic AI represents a user turn as a ``ModelRequest`` containing a
    ``UserPromptPart``.  ``UserPromptPart.content`` may be a plain string or a
    sequence of content blocks (text, images, files); only string text blocks
    are extracted and joined.

    Args:
        messages: The message history handed to a history processor.

    Returns:
        The latest user message text, or ``None`` if there is no user prompt
        with extractable text.
    """
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        texts: list[str] = []
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                texts.extend(_user_part_texts(part))
        if texts:
            return " ".join(texts).strip()
    return None


def _user_part_texts(part: UserPromptPart) -> list[str]:
    """Extract plain-text fragments from a ``UserPromptPart``."""
    content = part.content
    if isinstance(content, str):
        return [content] if content else []
    texts: list[str] = []
    for block in content:
        # Multimodal user content interleaves str text with binary/url blocks.
        if isinstance(block, str) and block:
            texts.append(block)
    return texts


def model_messages_to_zep(
    messages: list[ModelMessage],
    *,
    user_name: str | None,
    assistant_name: str,
) -> list[Message]:
    """Convert Pydantic AI messages into Zep ``Message`` objects.

    Only conversational text is persisted:

    * ``UserPromptPart`` (string text) -> a Zep ``user`` message.
    * ``TextPart`` on a ``ModelResponse`` -> a Zep ``assistant`` message.

    Tool calls, tool returns, system prompts, and non-text content are skipped
    -- Zep ingests the conversation, not the model's internal tool-use
    scaffolding.  Empty messages are dropped.

    Args:
        messages: Pydantic AI messages (e.g. from ``result.new_messages()``).
        user_name: Display name for user messages (may be ``None``).
        assistant_name: Display name for assistant messages.

    Returns:
        A list of Zep ``Message`` objects ready for ``thread.add_messages``.
    """
    out: list[Message] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    text = " ".join(_user_part_texts(part)).strip()
                    if text:
                        out.append(
                            Message(
                                role="user",
                                content=truncate_message_content(text, label="user turn"),
                                name=user_name,
                            )
                        )
        elif isinstance(message, ModelResponse):
            texts = [
                part.content
                for part in message.parts
                if isinstance(part, TextPart) and part.content
            ]
            text = " ".join(texts).strip()
            if text:
                out.append(
                    Message(
                        role="assistant",
                        content=truncate_message_content(text, label="assistant turn"),
                        name=assistant_name,
                    )
                )
    return out


def make_context_request(context: str) -> ModelRequest:
    """Wrap a Zep context block in a ``ModelRequest`` with a ``SystemPromptPart``.

    The returned request is prepended to the message history by the processor so
    the model sees Zep's memory before the conversation.

    Args:
        context: The Zep context block (facts, summaries, entities).

    Returns:
        A ``ModelRequest`` carrying a single system-prompt part.
    """
    instruction = (
        "The following context is retrieved from Zep's long-term memory "
        "service. It contains relevant facts, relationships, and prior "
        "knowledge about the user. Use it to inform your responses.\n\n"
        "<ZEP_CONTEXT>\n"
        f"{context}\n"
        "</ZEP_CONTEXT>"
    )
    return ModelRequest(parts=[SystemPromptPart(content=instruction)])


async def ensure_user_and_thread(deps: ZepDeps) -> bool:
    """Create the Zep user and thread if they do not already exist.

    Creation is idempotent: "already exists" conflicts are treated as success,
    and the ``(user_id, thread_id)`` pair is cached on ``deps`` so subsequent
    turns skip the round-trips.  A genuine failure (network, auth) returns
    ``False`` and is logged -- it never raises.

    Args:
        deps: The dependency object carrying the client and identity.

    Returns:
        ``True`` if the user and thread are ready, ``False`` on genuine failure.
    """
    key = (deps.user_id, deps.thread_id)
    if key in deps._created:
        return True

    if not await _ensure_user(deps):
        return False
    if not await _ensure_thread(deps):
        return False

    deps._created.add(key)
    return True


def _is_already_exists(exc: Exception) -> bool:
    text = str(exc).lower()
    return "already exists" in text or "conflict" in text or "409" in text


async def _ensure_user(deps: ZepDeps) -> bool:
    try:
        await deps.client.user.add(
            user_id=deps.user_id,
            first_name=deps.first_name,
            last_name=deps.last_name,
            email=deps.email,
        )
        logger.info("Created Zep user: %s", deps.user_id)
        return True
    except Exception as exc:
        if _is_already_exists(exc):
            logger.debug("Zep user %s already exists", deps.user_id)
            return True
        logger.warning("Failed to create Zep user %s: %s", deps.user_id, exc)
        return False


async def _ensure_thread(deps: ZepDeps) -> bool:
    try:
        await deps.client.thread.create(thread_id=deps.thread_id, user_id=deps.user_id)
        logger.info("Created Zep thread: %s", deps.thread_id)
        return True
    except Exception as exc:
        if _is_already_exists(exc):
            logger.debug("Zep thread %s already exists", deps.thread_id)
            return True
        logger.warning("Failed to create Zep thread %s: %s", deps.thread_id, exc)
        return False
