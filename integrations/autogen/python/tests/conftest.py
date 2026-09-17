"""Shared test helpers for the zep-autogen test suite."""

from collections.abc import AsyncIterator
from typing import Any


class FakePager:
    """A minimal stand-in for the ``AsyncPager`` of the Zep v4 SDK.

    The v4 list and search methods return a pager that yields the items of
    every page. The tests only need the asynchronous iteration.
    """

    def __init__(self, items: list[Any] | None = None) -> None:
        self.items = list(items or [])

    async def __aiter__(self) -> AsyncIterator[Any]:
        for item in self.items:
            yield item
