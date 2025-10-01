#!/usr/bin/env python3
"""
Tests for checkpoint functionality in ingestion
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ingestion import IngestionRunner


@pytest.fixture
def sample_dataframe():
    """Create a sample DataFrame for testing"""
    return pd.DataFrame(
        {
            "haystack_sessions": [
                [
                    [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}],
                    [
                        {"role": "user", "content": "How are you?"},
                        {"role": "assistant", "content": "Good"},
                    ],
                ],
                [
                    [{"role": "user", "content": "Test"}],
                ],
            ],
            "haystack_dates": [
                ["2024/01/01 (Mon) 10:00", "2024/01/01 (Mon) 11:00"],
                ["2024/01/02 (Tue) 10:00"],
            ],
            "question_type": ["single-session-preference", "single-session-qa"],
        }
    )


@pytest.mark.asyncio
async def test_checkpoint_creation():
    """Test that checkpoint file is created on first run"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_file = os.path.join(tmpdir, "checkpoint.json")

        with patch("ingestion.create_zep_client"):
            runner = IngestionRunner(checkpoint_file=checkpoint_file)

            # Verify checkpoint was initialized
            assert runner.checkpoint is not None
            assert runner.checkpoint["completed_users"] == []
            assert runner.checkpoint["failed_users"] == []
            assert "last_updated" in runner.checkpoint


@pytest.mark.asyncio
async def test_checkpoint_save_and_load():
    """Test that checkpoint is saved and can be loaded"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_file = os.path.join(tmpdir, "checkpoint.json")

        with patch("ingestion.create_zep_client"):
            # Create runner and modify checkpoint
            runner = IngestionRunner(checkpoint_file=checkpoint_file)
            runner.checkpoint["completed_users"] = [0, 1, 2]
            runner.checkpoint["failed_users"] = [3]
            runner._save_checkpoint()

            # Verify file was created
            assert os.path.exists(checkpoint_file)

            # Create new runner and verify it loaded the checkpoint
            runner2 = IngestionRunner(checkpoint_file=checkpoint_file)
            assert runner2.checkpoint["completed_users"] == [0, 1, 2]
            assert runner2.checkpoint["failed_users"] == [3]


@pytest.mark.asyncio
async def test_fresh_ingestion_resets_checkpoint(sample_dataframe):
    """Test that fresh ingestion resets the checkpoint"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_file = os.path.join(tmpdir, "checkpoint.json")

        with patch("ingestion.create_zep_client") as mock_client:
            mock_zep = MagicMock()
            mock_zep.user.add = AsyncMock()
            mock_zep.thread.create = AsyncMock()
            mock_zep.thread.add_messages = AsyncMock()
            mock_client.return_value = mock_zep

            # Create runner with existing checkpoint
            runner = IngestionRunner(checkpoint_file=checkpoint_file)
            runner.checkpoint["completed_users"] = [0, 1]
            runner._save_checkpoint()

            # Run fresh ingestion
            await runner.ingest_data(
                sample_dataframe,
                num_users=1,
                continue_from_checkpoint=False,
            )

            # Verify checkpoint was reset
            assert len(runner.checkpoint["completed_users"]) == 1  # Only new user
            assert 0 in runner.checkpoint["completed_users"]


@pytest.mark.asyncio
async def test_continue_from_checkpoint(sample_dataframe):
    """Test that ingestion can continue from checkpoint with additional users"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_file = os.path.join(tmpdir, "checkpoint.json")

        with patch("ingestion.create_zep_client") as mock_client:
            mock_zep = MagicMock()
            mock_zep.user.add = AsyncMock()
            mock_zep.thread.create = AsyncMock()
            mock_zep.thread.add_messages = AsyncMock()
            mock_client.return_value = mock_zep

            # Create runner with existing checkpoint (1 user completed)
            runner = IngestionRunner(checkpoint_file=checkpoint_file)
            runner.checkpoint["completed_users"] = [0]
            runner._save_checkpoint()

            # Continue ingestion with 1 additional user
            # Should process user 1 (since 0 is already done)
            await runner.ingest_data(
                sample_dataframe,
                num_users=1,
                continue_from_checkpoint=True,
            )

            # Verify user 1 was processed
            assert 0 in runner.checkpoint["completed_users"]
            assert 1 in runner.checkpoint["completed_users"]
            assert len(runner.checkpoint["completed_users"]) == 2


@pytest.mark.asyncio
async def test_failed_user_tracking(sample_dataframe):
    """Test that failed users are tracked in checkpoint and user is deleted"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_file = os.path.join(tmpdir, "checkpoint.json")

        with patch("ingestion.create_zep_client") as mock_client:
            mock_zep = MagicMock()
            mock_zep.user.add = AsyncMock()
            mock_zep.user.delete = AsyncMock()
            mock_zep.thread.create = AsyncMock()
            # Make add_messages fail
            mock_zep.thread.add_messages = AsyncMock(side_effect=Exception("API error"))
            mock_client.return_value = mock_zep

            runner = IngestionRunner(checkpoint_file=checkpoint_file)

            # Run ingestion (should fail)
            await runner.ingest_data(sample_dataframe, num_users=1)

            # Verify user was marked as failed
            assert 0 in runner.checkpoint["failed_users"]
            assert 0 not in runner.checkpoint["completed_users"]

            # Verify user was deleted from Zep
            mock_zep.user.delete.assert_called_once_with(user_id="lme_s_experiment_user_0")


@pytest.mark.asyncio
async def test_checkpoint_excludes_failed_users_on_continue(sample_dataframe):
    """Test that failed users are not retried when continuing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_file = os.path.join(tmpdir, "checkpoint.json")

        with patch("ingestion.create_zep_client") as mock_client:
            mock_zep = MagicMock()
            mock_zep.user.add = AsyncMock()
            mock_zep.thread.create = AsyncMock()
            mock_zep.thread.add_messages = AsyncMock()
            mock_client.return_value = mock_zep

            # Create checkpoint with completed and failed users (both 0 and 1 already processed)
            runner = IngestionRunner(checkpoint_file=checkpoint_file)
            runner.checkpoint["completed_users"] = [0]
            runner.checkpoint["failed_users"] = [1]
            runner._save_checkpoint()

            # Continue ingestion with 0 additional users (already processed = 2)
            # Should not process anything since target is 2 and we already have 2
            await runner.ingest_data(
                sample_dataframe,
                num_users=0,
                continue_from_checkpoint=True,
            )

            # Verify no new users were processed
            assert len(runner.checkpoint["completed_users"]) == 1
            assert len(runner.checkpoint["failed_users"]) == 1
