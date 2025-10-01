#!/usr/bin/env python3
"""
LongMemEval Benchmark - Ingestion and evaluation script
"""

import argparse
import asyncio
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from common import BenchmarkMetrics, EvaluationResult
from config import BenchmarkConfig
from constants import DEFAULT_CONCURRENCY
from evaluation import EvaluationRunner
from ingestion import IngestionRunner
from persistence import ResultsPersistence
from utils import setup_logging


def load_dataset(dataset_path: str, logger) -> pd.DataFrame:
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


async def run_ingestion(args, logger):
    """Run data ingestion"""
    print("Starting data ingestion")

    # Load configuration for concurrency setting
    config_path = "benchmark_config.yaml"
    try:
        benchmark_config = BenchmarkConfig.from_yaml(config_path)
        concurrency = benchmark_config.concurrency
    except FileNotFoundError:
        logger.warning(f"Configuration file {config_path} not found, using default concurrency")
        concurrency = DEFAULT_CONCURRENCY
    except Exception as e:
        logger.warning(f"Error loading configuration: {e}, using default concurrency")
        concurrency = DEFAULT_CONCURRENCY

    runner = IngestionRunner(log_level=args.log_level, concurrency=concurrency)

    if not args.skip_download:
        print("Downloading dataset...")
        await runner.download_dataset()

    print("Loading dataset...")
    df = load_dataset(args.dataset, logger)

    await runner.ingest_data(
        df,
        args.num_users,
        continue_from_checkpoint=args.continue_ingestion,
    )


async def run_evaluation(args, logger):
    """Run evaluation"""
    print("Starting evaluation")

    # Load configuration
    config_path = "benchmark_config.yaml"
    try:
        benchmark_config = BenchmarkConfig.from_yaml(config_path)
    except FileNotFoundError:
        print(f"Error: Configuration file {config_path} not found")
        return
    except Exception as e:
        print(f"Error: Invalid configuration: {e}")
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

    num_users = min(args.num_users, len(df))
    print(f"Evaluating {num_users} users")

    with tqdm(total=num_users, desc="Evaluating", unit="user") as pbar:
        for i in range(num_users):
            try:
                result, correct, duration, retrieval_duration = await runner.evaluate_conversation(
                    df, i
                )
                results.append(result)
                correct_count += correct
                total_duration += duration
                total_retrieval_duration += retrieval_duration

                # Update progress bar with current accuracy
                current_accuracy = correct_count / len(results) if results else 0
                pbar.set_postfix({"accuracy": f"{current_accuracy:.3f}", "correct": correct_count})
                pbar.update(1)

            except Exception as e:
                logger.error(f"Error processing user {i}: {e}")
                pbar.update(1)
                continue

    # Calculate metrics
    metrics = BenchmarkMetrics(
        accuracy=correct_count / len(results) if results else 0,
        correct_count=correct_count,
        total_count=len(results),
        avg_response_duration=total_duration / len(results) if results else 0,
        avg_retrieval_duration=(total_retrieval_duration / len(results) if results else 0),
    )

    # Save results
    persistence = ResultsPersistence(args.experiments_dir)
    run_dir = persistence.save_run(benchmark_config, metrics, results, config_path)

    # Print summary
    print("\nEvaluation completed:")
    print(f"  Accuracy: {metrics.accuracy:.3f} ({metrics.correct_count}/{metrics.total_count})")
    print(f"  Avg response time: {metrics.avg_response_duration:.3f}s")
    print(f"  Avg retrieval time: {metrics.avg_retrieval_duration:.3f}s")
    print(f"  Results saved to: {run_dir}")


async def main():
    parser = argparse.ArgumentParser(
        description="LongMemEval benchmark for Zep",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Mode selection (mutually exclusive)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--download", action="store_true", help="Download dataset only")
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
    parser.add_argument(
        "--continue",
        dest="continue_ingestion",
        action="store_true",
        help="Continue from previous checkpoint (ingestion only)",
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
    if args.download:
        logger.info("Starting dataset download")
        runner = IngestionRunner(log_level=args.log_level, concurrency=DEFAULT_CONCURRENCY)
        await runner.download_dataset()
        logger.info("Dataset download completed")
    elif args.ingest:
        await run_ingestion(args, logger)
    elif args.eval:
        await run_evaluation(args, logger)


if __name__ == "__main__":
    asyncio.run(main())
