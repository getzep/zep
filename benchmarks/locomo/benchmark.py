"""Unified CLI for LOCOMO evaluation harness."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep

from config import load_config
from evaluation import EvaluationRunner
from ingestion import IngestionRunner
from persistence import ResultsPersistence


def setup_logging(log_level: str) -> logging.Logger:
    """Setup logging configuration."""
    logger = logging.getLogger("locomo")
    logger.setLevel(getattr(logging, log_level.upper()))

    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


async def ingest_data(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Ingest LOCOMO data into Zep using graph API."""
    # Load config
    config = load_config(args.config)

    # Initialize Zep client
    zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    # Create ingestion runner
    ingestion_runner = IngestionRunner(config, zep, logger, prefix=args.prefix)

    # Ingest LOCOMO dataset
    await ingestion_runner.ingest_locomo()

    logger.info("Ingestion complete!")


async def evaluate_data(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Run LOCOMO evaluation using graph API."""
    # Load config
    config = load_config(args.config)

    # Print experimental setup
    print("\n" + "=" * 70)
    print("LOCOMO EVALUATION - EXPERIMENTAL SETUP")
    print("=" * 70)
    print(f"\nConfiguration:")
    print(f"  Config file: {args.config}")
    print(f"\nDataset:")
    print(f"  Dataset: LOCOMO")
    print(f"  Num graphs: {config.locomo.num_users}")
    print(f"  Max sessions per graph: {config.locomo.max_session_count}")
    print(f"\nGraph Retrieval:")
    print(f"  Edge limit: {config.graph_params.edge_limit}")
    print(f"  Edge reranker: {config.graph_params.edge_reranker}")
    print(f"  Node limit: {config.graph_params.node_limit}")
    print(f"  Node reranker: {config.graph_params.node_reranker}")
    print(f"\nModels:")
    print(f"  Response model: {config.models.response_model}")
    print(f"  Response temperature: {config.models.response_temperature}")
    print(f"  Grader model: {config.models.grader_model}")
    print(f"  Grader temperature: {config.models.grader_temperature}")
    print(f"\nEvaluation:")
    print(f"  Evaluation concurrency: {config.evaluation_concurrency}")
    print(f"  Number of runs: {args.num_runs}")
    print("=" * 70 + "\n")

    # Initialize clients
    zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
    openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Create runners
    evaluation_runner = EvaluationRunner(config, zep, openai_client, logger, prefix=args.prefix)
    persistence = ResultsPersistence(config, logger)

    # Load LOCOMO data
    import pandas as pd

    data_path = Path("data") / "locomo.json"
    if not data_path.exists():
        logger.error(f"LOCOMO data not found at {data_path}. Run ingestion first.")
        sys.exit(1)

    df = pd.read_json(data_path)

    # Run evaluation multiple times
    all_run_metrics = []
    all_run_dirs = []
    all_run_results = []  # Collect raw results from all runs

    # Create experiment directory for multi-run experiments
    experiment_dir = None
    if args.num_runs > 1:
        experiment_dir = persistence.save_experiment(args.config)
        logger.info(f"Created experiment directory: {experiment_dir}")

    for run_num in range(1, args.num_runs + 1):
        if args.num_runs > 1:
            print(f"\n{'=' * 70}")
            print(f"STARTING RUN {run_num}/{args.num_runs}")
            print(f"{'=' * 70}\n")
            logger.info(f"Starting evaluation run {run_num}/{args.num_runs}")

        # Run evaluation
        results = await evaluation_runner.evaluate_locomo(df)

        # Save results
        if experiment_dir is not None:
            # Multi-run: save to experiment directory
            run_dir = persistence.save_run(
                results, args.config, run_number=run_num, experiment_dir=experiment_dir
            )
            # Collect raw results for aggregation
            all_run_results.extend(results)
        else:
            # Single run: use timestamped directory
            run_dir = persistence.save_run(results, args.config)

        all_run_dirs.append(run_dir)

        # Calculate metrics
        metrics = persistence._calculate_metrics(results)
        all_run_metrics.append(metrics)

    # Save experiment summary for multi-run experiments
    if experiment_dir is not None:
        persistence.save_experiment_summary(experiment_dir, all_run_metrics, all_run_results)

    # Print summary output
    if args.num_runs == 1:
        # Single run - print detailed metrics
        metrics = all_run_metrics[0]
        print("\n" + "=" * 70)
        print("EVALUATION RESULTS SUMMARY")
        print("=" * 70)
        print(f"\nAccuracy: {metrics.accuracy:.3f} ({metrics.correct_count}/{metrics.total_count})")

        print("\nContext Completeness:")
        print(
            f"  COMPLETE: {metrics.completeness_complete_rate:.3f} "
            f"({metrics.completeness_complete_count}/{metrics.total_count})"
        )
        print(
            f"  PARTIAL: {metrics.completeness_partial_rate:.3f} "
            f"({metrics.completeness_partial_count}/{metrics.total_count})"
        )
        print(
            f"  INSUFFICIENT: {metrics.completeness_insufficient_rate:.3f} "
            f"({metrics.completeness_insufficient_count}/{metrics.total_count})"
        )
        if metrics.accuracy_with_complete_context is not None:
            print(
                f"  Accuracy w/ Complete Context: {metrics.accuracy_with_complete_context:.3f} "
                f"({metrics.correct_with_complete_context}/{metrics.total_with_complete_context})"
            )

        print("\nLatency Statistics:")
        print(
            f"  Response time - median: {metrics.response_duration_stats.median:.3f}s, "
            f"p95: {metrics.response_duration_stats.p95:.3f}s, "
            f"p99: {metrics.response_duration_stats.p99:.3f}s"
        )
        print(
            f"  Retrieval time - median: {metrics.retrieval_duration_stats.median:.3f}s, "
            f"p95: {metrics.retrieval_duration_stats.p95:.3f}s, "
            f"p99: {metrics.retrieval_duration_stats.p99:.3f}s"
        )

        print("\nContext Token Statistics:")
        print(
            f"  Tokens - median: {metrics.context_token_stats.median:.0f}, "
            f"mean: {metrics.context_token_stats.mean:.0f}, "
            f"p95: {metrics.context_token_stats.p95:.0f}, "
            f"p99: {metrics.context_token_stats.p99:.0f}"
        )

        print("\nBy Category:")
        for cat_metrics in metrics.by_category:
            print(
                f"  Category {cat_metrics.category}: {cat_metrics.accuracy:.3f} "
                f"({cat_metrics.correct_count}/{cat_metrics.total_count})"
            )

        print(f"\nResults saved to: {all_run_dirs[0]}")
        print("=" * 70 + "\n")
    else:
        # Multiple runs - print aggregated statistics
        from statistics import mean, stdev

        print("\n" + "=" * 70)
        print(f"EVALUATION RESULTS SUMMARY - {args.num_runs} RUNS")
        print("=" * 70)

        # Aggregate accuracy statistics
        accuracies = [m.accuracy for m in all_run_metrics]
        print(f"\nAccuracy:")
        print(f"  Mean: {mean(accuracies):.3f}")
        if len(accuracies) > 1:
            print(f"  Std Dev: {stdev(accuracies):.3f}")
        print(f"  Min: {min(accuracies):.3f}")
        print(f"  Max: {max(accuracies):.3f}")
        print(f"  Runs: {[f'{a:.3f}' for a in accuracies]}")

        # Aggregate completeness statistics
        complete_rates = [m.completeness_complete_rate for m in all_run_metrics]
        partial_rates = [m.completeness_partial_rate for m in all_run_metrics]
        insufficient_rates = [m.completeness_insufficient_rate for m in all_run_metrics]

        print(f"\nContext Completeness (Mean):")
        print(f"  COMPLETE: {mean(complete_rates):.3f}")
        print(f"  PARTIAL: {mean(partial_rates):.3f}")
        print(f"  INSUFFICIENT: {mean(insufficient_rates):.3f}")

        # Aggregate accuracy with complete context
        complete_ctx_accuracies = [m.accuracy_with_complete_context for m in all_run_metrics if m.accuracy_with_complete_context is not None]
        if complete_ctx_accuracies:
            print(f"\nAccuracy w/ Complete Context:")
            print(f"  Mean: {mean(complete_ctx_accuracies):.3f}")
            if len(complete_ctx_accuracies) > 1:
                print(f"  Std Dev: {stdev(complete_ctx_accuracies):.3f}")

        # Per-run details
        print(f"\nPer-Run Results:")
        for idx, metrics in enumerate(all_run_metrics, 1):
            print(f"  Run {idx}: Accuracy={metrics.accuracy:.3f}, Complete={metrics.completeness_complete_rate:.3f}")

        # Show experiment directory location
        print(f"\nExperiment directory: {experiment_dir}")
        print(f"Experiment summary: {experiment_dir / 'experiment_summary.json'}")
        print(f"Configuration: {experiment_dir / 'config.yaml'}")
        print(f"Run results: {', '.join([f'run_{i+1}_results.json' for i in range(args.num_runs)])}")

        print("\n" + "=" * 70 + "\n")

    logger.info(f"Evaluation complete. {args.num_runs} run(s) saved.")


async def cleanup_users(args: argparse.Namespace, logger: logging.Logger) -> None:
    """List and optionally delete all graphs from Zep with the specified prefix."""
    # Initialize Zep client
    zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

    logger.info("Fetching all graphs...")

    # List all graphs with pagination
    all_graphs = []
    page_number = 1
    page_size = 100

    while True:
        result = await zep.graph.list(page_size=page_size, page_number=page_number)
        if not result.graphs:
            break
        all_graphs.extend(result.graphs)
        page_number += 1

        # Break if we've fetched all graphs
        if len(result.graphs) < page_size:
            break

    # Filter for graphs with the specified prefix
    prefix_pattern = f"{args.prefix}_experiment_graph_"
    prefix_graphs = [g for g in all_graphs if g.graph_id.startswith(prefix_pattern)]

    if not prefix_graphs:
        logger.info(f"No graphs found with prefix '{args.prefix}'.")
        return

    logger.info(f"Found {len(prefix_graphs)} graphs with prefix '{args.prefix}':")
    for graph in prefix_graphs:
        logger.info(f"  - {graph.graph_id}")

    # Ask for confirmation if delete flag is set
    if args.delete:
        logger.warning(f"About to delete {len(prefix_graphs)} graphs with prefix '{args.prefix}'.")
        confirmation = input("Type 'yes' to confirm deletion: ")
        if confirmation.lower() != "yes":
            logger.info("Deletion cancelled.")
            return

        # Delete graphs
        logger.info("Deleting graphs...")
        deleted_count = 0
        for graph in prefix_graphs:
            try:
                await zep.graph.delete(graph.graph_id)
                deleted_count += 1
                logger.debug(f"Deleted graph: {graph.graph_id}")
            except Exception as e:
                logger.error(f"Failed to delete graph {graph.graph_id}: {e}")

        logger.info(f"Successfully deleted {deleted_count}/{len(prefix_graphs)} graphs.")
    else:
        logger.info("Use --delete flag to delete these graphs.")


def main() -> None:
    """Main CLI entry point."""
    # Load environment variables
    load_dotenv()

    # Create parser
    parser = argparse.ArgumentParser(
        description="LOCOMO Evaluation Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ingest LOCOMO data using graph API
  python benchmark.py --ingest

  # Run single evaluation
  python benchmark.py --eval

  # Run multiple evaluations (creates separate experiment for each run)
  python benchmark.py --eval --num-runs 3

  # Use custom prefix for namespacing experiments
  python benchmark.py --ingest --prefix experiment_a
  python benchmark.py --eval --prefix experiment_a

  # Run multiple evaluations with custom config
  python benchmark.py --eval --num-runs 5 --config benchmark_config.yaml

  # List LOCOMO graphs (default prefix)
  python benchmark.py --cleanup

  # List graphs with custom prefix
  python benchmark.py --cleanup --prefix experiment_a

  # Delete LOCOMO graphs
  python benchmark.py --cleanup --delete

  # Delete graphs with custom prefix
  python benchmark.py --cleanup --prefix experiment_a --delete

  # Run with debug logging
  python benchmark.py --eval --log-level DEBUG
        """,
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--ingest", action="store_true", help="Ingest data into Zep using graph API")
    mode_group.add_argument("--eval", action="store_true", help="Run evaluation")
    mode_group.add_argument(
        "--cleanup", action="store_true", help="List or delete LOCOMO graphs from Zep"
    )

    # Common arguments
    parser.add_argument(
        "--config",
        type=str,
        default="benchmark_config.yaml",
        help="Path to configuration file (default: benchmark_config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="locomo",
        help="Prefix for user/graph names to namespace experiments (default: locomo)",
    )

    # Evaluation-specific arguments
    parser.add_argument(
        "--num-runs",
        type=int,
        default=1,
        help="Number of evaluation runs to perform (default: 1). Each run creates a separate experiment.",
    )

    # Cleanup-specific arguments
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete users when using --cleanup (requires confirmation)",
    )

    # Parse arguments
    args = parser.parse_args()

    # Setup logging
    logger = setup_logging(args.log_level)

    # Run appropriate mode
    try:
        if args.ingest:
            asyncio.run(ingest_data(args, logger))
        elif args.eval:
            asyncio.run(evaluate_data(args, logger))
        elif args.cleanup:
            asyncio.run(cleanup_users(args, logger))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
