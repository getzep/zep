"""Thin wrappers for graph endpoints not yet exposed on the installed zep-cloud SDK."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError
from zep_cloud.core.pydantic_utilities import parse_obj_as
from zep_cloud.graph.raw_client import OMIT
from zep_cloud.types.episode import Episode as ZepEpisode

from zep_ingest.types import Destination, Episode, to_graph_add_kwargs


def _raise_api_error(response: Any) -> None:
    raise ApiError(
        status_code=response.status_code,
        body=response.text,
        headers=dict(response.headers),
    )


def graph_add(client: Zep, episode: Episode, destination: Destination) -> ZepEpisode:
    """Submit one graph episode, including ``document_id`` when set."""
    kwargs = to_graph_add_kwargs(episode, destination)
    document_id = kwargs.pop("document_id", None)
    if document_id is None:
        return client.graph.add(**kwargs)

    wrapper = client.graph.with_raw_response._client_wrapper
    body: dict[str, Any] = {
        "data": kwargs["data"],
        "type": kwargs["type"],
        "created_at": kwargs.get("created_at", OMIT),
        "graph_id": kwargs.get("graph_id", OMIT),
        "metadata": kwargs.get("metadata", OMIT),
        "user_id": kwargs.get("user_id", OMIT),
        "document_id": document_id,
    }
    response = wrapper.httpx_client.request(
        "graph",
        method="POST",
        json=body,
        headers={"content-type": "application/json"},
        omit=OMIT,
    )
    if 200 <= response.status_code < 300:
        return parse_obj_as(ZepEpisode, response.json())
    _raise_api_error(response)
    raise AssertionError("unreachable")


def get_document_episodes(
    client: Zep,
    *,
    graph_id: str,
    document_id: str,
) -> list[dict[str, Any]]:
    """List episodes grouped under a document (v3 ``get_episodes_for_document``)."""
    wrapper = client.graph.with_raw_response._client_wrapper
    encoded = quote(document_id, safe="")
    response = wrapper.httpx_client.request(
        f"graph/documents/{encoded}/episodes",
        method="GET",
        params={"graph_id": graph_id},
    )
    if 200 <= response.status_code < 300:
        payload = response.json()
        episodes = payload.get("episodes") if isinstance(payload, dict) else None
        return list(episodes or [])
    _raise_api_error(response)
    raise AssertionError("unreachable")
