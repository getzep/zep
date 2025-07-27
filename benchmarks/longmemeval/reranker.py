#!/usr/bin/env python3
"""
Reranking service for LongMemEval benchmark
"""

import logging
from typing import List, Union
from dataclasses import dataclass

from zep_cloud import EntityEdge, EntityNode, Episode

try:
    from mxbai_rerank import MxbaiRerankV2

    MXBAI_AVAILABLE = True
except ImportError:
    MXBAI_AVAILABLE = False
    MxbaiRerankV2 = None

try:
    import voyageai

    VOYAGE_AVAILABLE = True
except ImportError:
    VOYAGE_AVAILABLE = False


@dataclass
class RerankResult:
    """Result from reranking operation"""

    item: Union[EntityEdge, EntityNode, Episode]
    score: float
    original_index: int


class MxbaiRerankerService:
    """Service for reranking using MxbaiRerankV2. Requires HF_TOKEN to be set in the environment."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        if not MXBAI_AVAILABLE:
            raise ImportError(
                "mixedbread-ai packages not available. Install with: pip install mixedbread-ai"
            )

        try:
            self.logger.info("Successfully authenticated with Hugging Face Hub")
        except Exception as e:
            self.logger.warning(f"Failed to authenticate with Hugging Face Hub: {e}")
            self.logger.warning(
                "Proceeding without authentication - model download may fail"
            )

        # Initialize the reranker model using package's auto device detection
        try:
            from mxbai_rerank.utils import auto_device

            device = auto_device()
            self.logger.info(f"MxbaiRerankV2 auto-detected device: {device}")

            self.reranker = MxbaiRerankV2(
                "mixedbread-ai/mxbai-rerank-base-v2", device=device
            )  # type: ignore
            self.logger.info(f"Initialized MxbaiRerankV2 reranker on {device}")
        except Exception as e:
            self.logger.error(f"Failed to initialize MxbaiRerankV2: {e}")
            raise

    def _extract_text_from_edge(self, edge: EntityEdge) -> str:
        """Extract searchable text from an EntityEdge"""
        return edge.fact or ""

    def _extract_text_from_node(self, node: EntityNode) -> str:
        """Extract searchable text from an EntityNode"""
        parts = []
        if node.name:
            parts.append(node.name)
        if node.summary:
            parts.append(node.summary)
        if node.attributes:
            parts.append(str(node.attributes))
        return " ".join(parts)

    def _extract_text_from_episode(self, episode: Episode) -> str:
        """Extract searchable text from an Episode"""
        return episode.content or ""

    def rerank_edges(
        self, query: str, edges: List[EntityEdge], top_k: int
    ) -> List[EntityEdge]:
        """Rerank edges using MxbaiRerankV2"""
        if not edges:
            return edges

        # Extract texts for reranking
        documents = [self._extract_text_from_edge(edge) for edge in edges]

        # Perform reranking
        results = self.reranker.rank(query=query, documents=documents, top_k=top_k)

        # Sort by relevance score and return top-k edges
        # RankResult objects have .index and .score attributes
        reranked_edges = []
        for result in results:
            original_idx = result.index
            if 0 <= original_idx < len(edges):
                reranked_edges.append(edges[original_idx])

        self.logger.debug(f"Reranked {len(edges)} edges to top {len(reranked_edges)}")
        return reranked_edges[:top_k]

    def rerank_nodes(
        self, query: str, nodes: List[EntityNode], top_k: int
    ) -> List[EntityNode]:
        """Rerank nodes using MxbaiRerankV2"""
        if not nodes:
            return nodes

        # Extract texts for reranking
        documents = [self._extract_text_from_node(node) for node in nodes]

        # Perform reranking
        results = self.reranker.rank(query=query, documents=documents, top_k=top_k)

        # Sort by relevance score and return top-k nodes
        # RankResult objects have .index and .score attributes
        reranked_nodes = []
        for result in results:
            original_idx = result.index
            if 0 <= original_idx < len(nodes):
                reranked_nodes.append(nodes[original_idx])

        self.logger.debug(f"Reranked {len(nodes)} nodes to top {len(reranked_nodes)}")
        return reranked_nodes[:top_k]

    def rerank_episodes(
        self, query: str, episodes: List[Episode], top_k: int
    ) -> List[Episode]:
        """Rerank episodes using MxbaiRerankV2"""
        if not episodes:
            return episodes

        # Extract texts for reranking
        documents = [self._extract_text_from_episode(episode) for episode in episodes]

        # Perform reranking
        results = self.reranker.rank(query=query, documents=documents, top_k=top_k)

        # Sort by relevance score and return top-k episodes
        # RankResult objects have .index and .score attributes
        reranked_episodes = []
        for result in results:
            original_idx = result.index
            if 0 <= original_idx < len(episodes):
                reranked_episodes.append(episodes[original_idx])

        self.logger.debug(
            f"Reranked {len(episodes)} episodes to top {len(reranked_episodes)}"
        )
        return reranked_episodes[:top_k]


class VoyageAIRerankerService:
    """Service for reranking using VoyageAI rerank-2.5 model. Requires VOYAGE_API_KEY to be set in the environment."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        if not VOYAGE_AVAILABLE:
            raise ImportError(
                "voyageai package not available. Install with: pip install voyageai"
            )

        # Initialize the VoyageAI client
        self.client = voyageai.Client()  # Uses VOYAGE_API_KEY from environment
        self.model = "rerank-2.5"
        self.logger.info("Initialized VoyageAI reranker with rerank-2.5 model")

    def _extract_text_from_edge(self, edge: EntityEdge) -> str:
        """Extract searchable text from an EntityEdge"""
        return edge.fact or ""

    def _extract_text_from_node(self, node: EntityNode) -> str:
        """Extract searchable text from an EntityNode"""
        parts = []
        if node.name:
            parts.append(node.name)
        if node.summary:
            parts.append(node.summary)
        if node.attributes:
            parts.append(str(node.attributes))
        return " ".join(parts)

    def _extract_text_from_episode(self, episode: Episode) -> str:
        """Extract searchable text from an Episode"""
        return episode.content or ""

    def rerank_edges(
        self, query: str, edges: List[EntityEdge], top_k: int
    ) -> List[EntityEdge]:
        """Rerank edges using VoyageAI rerank-2.5"""
        if not edges:
            return edges

        # Extract texts for reranking
        documents = [self._extract_text_from_edge(edge) for edge in edges]

        # Perform reranking
        reranking = self.client.rerank(
            query=query,
            documents=documents,
            model=self.model,
            top_k=min(top_k, len(documents)),
        )

        # Sort by relevance score and return top-k edges
        reranked_edges = []
        for result in reranking.results:
            # VoyageAI returns results with document indices
            original_idx = result.index
            if 0 <= original_idx < len(edges):
                reranked_edges.append(edges[original_idx])

        self.logger.debug(f"Reranked {len(edges)} edges to top {len(reranked_edges)}")
        return reranked_edges[:top_k]

    def rerank_nodes(
        self, query: str, nodes: List[EntityNode], top_k: int
    ) -> List[EntityNode]:
        """Rerank nodes using VoyageAI rerank-2.5"""
        if not nodes:
            return nodes

        # Extract texts for reranking
        documents = [self._extract_text_from_node(node) for node in nodes]

        # Perform reranking
        reranking = self.client.rerank(
            query=query,
            documents=documents,
            model=self.model,
            top_k=min(top_k, len(documents)),
        )

        # Sort by relevance score and return top-k nodes
        reranked_nodes = []
        for result in reranking.results:
            original_idx = result.index
            if 0 <= original_idx < len(nodes):
                reranked_nodes.append(nodes[original_idx])

        self.logger.debug(f"Reranked {len(nodes)} nodes to top {len(reranked_nodes)}")
        return reranked_nodes[:top_k]

    def rerank_episodes(
        self, query: str, episodes: List[Episode], top_k: int
    ) -> List[Episode]:
        """Rerank episodes using VoyageAI rerank-2.5"""
        if not episodes:
            return episodes

        # Extract texts for reranking
        documents = [self._extract_text_from_episode(episode) for episode in episodes]

        # Perform reranking
        reranking = self.client.rerank(
            query=query,
            documents=documents,
            model=self.model,
            top_k=min(top_k, len(documents)),
        )

        # Sort by relevance score and return top-k episodes
        reranked_episodes = []
        for result in reranking.results:
            original_idx = result.index
            if 0 <= original_idx < len(episodes):
                reranked_episodes.append(episodes[original_idx])

        self.logger.debug(
            f"Reranked {len(episodes)} episodes to top {len(reranked_episodes)}"
        )
        return reranked_episodes[:top_k]


class RerankerFactory:
    """Factory for creating reranker services"""

    @staticmethod
    def create_reranker(
        reranker_type: str,
    ) -> Union[MxbaiRerankerService, VoyageAIRerankerService, None]:
        """Create a reranker service based on type"""
        if reranker_type == "mxbai_rerank":
            return MxbaiRerankerService()
        elif reranker_type == "voyage_rerank":
            return VoyageAIRerankerService()
        else:
            return None

    @staticmethod
    def is_secondary_reranker(reranker_type: str) -> bool:
        """Check if this is a secondary (post-search) reranker"""
        return reranker_type in ["mxbai_rerank", "voyage_rerank"]
