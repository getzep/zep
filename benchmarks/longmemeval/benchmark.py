#!/usr/bin/env python3
"""
LongMemEval Benchmark - Ingestion and evaluation script
"""

import argparse
import asyncio
import logging
from pathlib import Path

import pandas as pd

from common import BenchmarkMetrics, EvaluationResult
from config import BenchmarkConfig
from evaluation import EvaluationRunner
from ingestion import IngestionRunner
from persistence import ResultsPersistence


def setup_logging(log_level: str) -> logging.Logger:
    """Configure logging"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


def load_dataset(dataset_path: str, logger: logging.Logger) -> pd.DataFrame:
    """Load the LongMemEval dataset"""
    logger.info(f"Loading dataset from {dataset_path}")
    path = Path(dataset_path)

    if path.exists():
        return pd.read_json(path)

    parent_path = Path("..") / path.name
    if parent_path.exists():
        logger.info(f"Using dataset from parent directory: {parent_path}")
        return pd.read_json(parent_path)

    raise FileNotFoundError(f"Dataset not found at {dataset_path} or {parent_path}")


async def run_ingestion(args, logger: logging.Logger):
    """Run data ingestion"""
    logger.info("Starting data ingestion")

    runner = IngestionRunner(log_level=args.log_level)

    if not args.skip_download:
        await runner.download_dataset()

    df = load_dataset(args.dataset, logger)
    await runner.ingest_data(df, args.num_users)

    logger.info(f"Ingestion completed: {args.num_users} users")


async def run_evaluation(args, logger: logging.Logger):
    """Run evaluation"""
    logger.info("Starting evaluation")

    # Load configuration
    config_path = "benchmark_config.yaml"
    try:
        benchmark_config = BenchmarkConfig.from_yaml(config_path)
    except FileNotFoundError:
        logger.error(f"Configuration file {config_path} not found")
        return
    except Exception as e:
        logger.error(f"Invalid configuration: {e}")
        return

    # Initialize runner
    runner = EvaluationRunner(
        log_level=args.log_level,
        config=benchmark_config,
    )

    # Load dataset
    df = load_dataset(args.dataset, logger)

    # Run evaluation
    results: list[EvaluationResult] = []
    correct_count = 0
    total_duration = 0.0
    total_retrieval_duration = 0.0

    logger.info(f"Evaluating {args.num_users} users")

    for i in range(min(args.num_users, len(df))):
        try:
            result, correct, duration, retrieval_duration = (
                await runner.evaluate_conversation(df, i)
            )
            results.append(result)
            correct_count += correct
            total_duration += duration
            total_retrieval_duration += retrieval_duration

            if (i + 1) % 50 == 0:
                logger.info(f"Processed {i + 1} users")

        except Exception as e:
            logger.error(f"Error processing user {i}: {e}")
            continue

    # Calculate metrics
    metrics = BenchmarkMetrics(
        accuracy=correct_count / len(results) if results else 0,
        correct_count=correct_count,
        total_count=len(results),
        avg_response_duration=total_duration / len(results) if results else 0,
        avg_retrieval_duration=(
            total_retrieval_duration / len(results) if results else 0
        ),
    )

    # Save results
    persistence = ResultsPersistence(args.experiments_dir)
    run_dir = persistence.save_run(benchmark_config, metrics, results, config_path)

    # Log summary
    logger.info("Evaluation completed:")
    logger.info(
        f"  Accuracy: {metrics.accuracy:.3f} ({metrics.correct_count}/{metrics.total_count})"
    )
    logger.info(f"  Avg response time: {metrics.avg_response_duration:.3f}s")
    logger.info(f"  Avg retrieval time: {metrics.avg_retrieval_duration:.3f}s")
    logger.info(f"  Results saved to: {run_dir}")


async def main():
    parser = argparse.ArgumentParser(
        description="LongMemEval benchmark for Zep",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Mode selection (mutually exclusive)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ingest", action="store_true", help="Run data ingestion")
    mode.add_argument("--eval", action="store_true", help="Run evaluation")

    # Common arguments
    parser.add_argument(
        "--dataset",
        default="data/longmemeval_s.json",
        help="Dataset file path (default: data/longmemeval_s.json)",
    )
    parser.add_argument(
        "--num-users",
        type=int,
        default=500,
        help="Number of users to process (default: 500)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: WARNING)",
    )

    # Ingestion-specific arguments
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip dataset download (ingestion only)",
    )

    # Evaluation-specific arguments
    parser.add_argument(
        "--experiments-dir",
        default="experiments",
        help="Results directory (default: experiments, evaluation only)",
    )

    args = parser.parse_args()
    logger = setup_logging(args.log_level)

    # Run selected mode
    if args.ingest:
        await run_ingestion(args, logger)
    elif args.eval:
        await run_evaluation(args, logger)


if __name__ == "__main__":
    asyncio.run(main())
