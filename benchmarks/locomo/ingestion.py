"""Data ingestion for LOCOMO evaluation harness using graph.add API."""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests
from tqdm.asyncio import tqdm
from zep_cloud.client import AsyncZep

from config import BenchmarkConfig
from constants import DATA_DIR
from ontology import ZEP_NODE_ONTOLOGY_V2


class IngestionRunner:
    """Handles data ingestion for LOCOMO dataset using graph.add API."""

    def __init__(
        self,
        config: BenchmarkConfig,
        zep_client: AsyncZep,
        logger: logging.Logger,
        prefix: str = "locomo",
    ):
        self.config = config
        self.zep = zep_client
        self.logger = logger
        self.prefix = prefix
        self._semaphore = asyncio.Semaphore(config.ingestion_concurrency)

    async def ingest_locomo(self) -> pd.DataFrame:
        """Ingest LOCOMO dataset."""
        self.logger.info("Downloading LOCOMO dataset...")

        # Download data
        url = self.config.locomo.data_url
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        locomo_df = pd.read_json(url)

        # Save locally
        os.makedirs(DATA_DIR, exist_ok=True)
        data_path = Path(DATA_DIR) / "locomo.json"
        with open(data_path, "w") as f:
            json.dump(data, f, indent=2)
        self.logger.info(f"Saved dataset to {data_path}")

        # Ingest into Zep
        self.logger.info(f"Ingesting {self.config.locomo.num_users} graphs...")
        tasks = []
        for group_idx in range(self.config.locomo.num_users):
            task = self._ingest_locomo_graph(locomo_df, group_idx)
            tasks.append(task)

        # Process with progress bar
        with tqdm(total=len(tasks), desc="Ingesting graphs (v2)", unit="graph") as pbar:
            for coro in asyncio.as_completed(tasks):
                await coro
                pbar.update(1)

        self.logger.info("LOCOMO ingestion (v2) complete")
        return locomo_df

    async def _ingest_locomo_graph(self, df: pd.DataFrame, group_idx: int) -> bool:
        """Ingest a single LOCOMO graph using graph.add API with graph_id."""
        async with self._semaphore:
            try:
                conversation = df["conversation"].iloc[group_idx]
                graph_id = f"{self.prefix}_experiment_graph_{group_idx}"

                # Create graph - ignore if exists
                try:
                    await self.zep.graph.create(
                        graph_id=graph_id,
                        name=f"LOCOMO Graph {group_idx}",
                        description=f"Multi-participant conversation graph for LOCOMO experiment {group_idx}",
                    )
                    self.logger.debug(f"Created graph: {graph_id}")
                except Exception as e:
                    self.logger.debug(f"Graph {graph_id} already exists: {e}")

                # Set ontology for this graph before adding any data
                try:
                    await self.zep.graph.set_ontology(
                        entities=ZEP_NODE_ONTOLOGY_V2,
                        edges={},
                        graph_ids=[graph_id],
                    )
                    self.logger.debug(f"Set ontology for graph: {graph_id}")
                except Exception as e:
                    self.logger.error(
                        f"Failed to set ontology for graph {graph_id}: {e}"
                    )
                    raise

                # Process each session - add messages directly to graph
                for session_idx in range(self.config.locomo.max_session_count):
                    session_key = f"session_{session_idx}"
                    session = conversation.get(session_key)
                    if session is None:
                        continue

                    # Parse session timestamp
                    session_date = (
                        conversation.get(f"session_{session_idx}_date_time") + " UTC"
                    )
                    date_format = "%I:%M %p on %d %B, %Y UTC"
                    date_string = datetime.strptime(session_date, date_format).replace(
                        tzinfo=UTC
                    )
                    iso_date = date_string.isoformat()

                    # Process each message in the session
                    for msg in session:
                        speaker = msg.get("speaker")
                        text = msg.get("text")
                        blip_caption = msg.get("blip_captions")

                        content = text
                        if blip_caption:
                            content += (
                                f" (description of attached image: {blip_caption})"
                            )

                        # Determine role based on speaker
                        role = "user" if speaker == "User" else "assistant"

                        # Format message as string for graph.add
                        message_data = f"{speaker} ({role}): {content}"

                        # Add message to graph using graph.add API
                        await self.zep.graph.add(
                            graph_id=graph_id,
                            type="message",
                            data=message_data,
                            created_at=iso_date,
                        )

                return True

            except Exception as e:
                self.logger.error(f"Failed to ingest LOCOMO graph {group_idx}: {e}")
                return False
