"""Results persistence for LOCOMO evaluation harness."""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

import orjson

from common import (
    BenchmarkMetrics,
    CategoryMetrics,
    DifficultyMetrics,
    EvaluationResult,
    LatencyStats,
    TokenStats,
)
from config import BenchmarkConfig
from constants import EXPERIMENTS_DIR


class ResultsPersistence:
    """Handles saving and loading evaluation results."""

    def __init__(self, config: BenchmarkConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.experiments_dir = Path(EXPERIMENTS_DIR)
        self.experiments_dir.mkdir(exist_ok=True)

    def save_experiment(self, config_file_path: str | None = None) -> Path:
        """Create experiment directory and save config."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiment_id = f"experiment_{timestamp}"
        experiment_dir = self.experiments_dir / experiment_id
        experiment_dir.mkdir(exist_ok=True)

        self.logger.info(f"Created experiment directory: {experiment_dir}")

        # Save config once at experiment level
        config_path = experiment_dir / "config.yaml"
        self.config.to_yaml(config_path)

        return experiment_dir

    def save_run(
        self,
        results: list[EvaluationResult],
        config_file_path: str | None = None,
        run_number: int | None = None,
        experiment_dir: Path | None = None,
    ) -> Path:
        """Save evaluation run with timestamped directory or to experiment directory."""
        # Calculate metrics
        metrics = self._calculate_metrics(results)

        if experiment_dir is not None:
            # Multi-run experiment: save to experiment directory
            results_path = experiment_dir / f"run_{run_number}_results.json"

            self.logger.info(f"Saving run {run_number} results to {results_path}")

            # Save results.json using orjson for fast serialization
            results_dict = {
                "run_number": run_number,
                "dataset": "locomo",
                "metrics": metrics.model_dump(),
                "results": [r.model_dump() for r in results],
            }
            with open(results_path, "wb") as f:
                f.write(orjson.dumps(results_dict))

            self.logger.info(f"Saved {len(results)} results for run {run_number}")
            self.logger.info(f"Run {run_number} Accuracy: {metrics.accuracy:.3f}")
            self.logger.info(f"Correct: {metrics.correct_count}/{metrics.total_count}")

            return experiment_dir
        else:
            # Single run: keep current behavior
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if run_number is not None:
                run_id = f"run_{timestamp}_iter_{run_number}"
            else:
                run_id = f"run_{timestamp}"
            run_dir = self.experiments_dir / run_id
            run_dir.mkdir(exist_ok=True)

            self.logger.info(f"Saving results to {run_dir}")

            # Save results.json using orjson for fast serialization
            results_path = run_dir / "results.json"
            results_dict = {
                "run_id": run_id,
                "timestamp": timestamp,
                "dataset": "locomo",
                "metrics": metrics.model_dump(),
                "results": [r.model_dump() for r in results],
            }
            with open(results_path, "wb") as f:
                f.write(orjson.dumps(results_dict))

            # Save config snapshot
            config_path = run_dir / "config.yaml"
            self.config.to_yaml(config_path)

            self.logger.info(f"Saved {len(results)} results to {results_path}")
            self.logger.info(f"Overall Accuracy: {metrics.accuracy:.3f}")
            self.logger.info(f"Correct: {metrics.correct_count}/{metrics.total_count}")

            return run_dir

    def _calculate_metrics(self, results: list[EvaluationResult]) -> BenchmarkMetrics:
        """Calculate aggregate metrics from results."""
        # Overall accuracy
        correct_count = sum(1 for r in results if r.grade)
        total_count = len(results)
        accuracy = correct_count / total_count if total_count > 0 else 0.0

        # Completeness metrics
        completeness_complete_count = sum(
            1 for r in results if r.completeness_grade == "COMPLETE"
        )
        completeness_partial_count = sum(
            1 for r in results if r.completeness_grade == "PARTIAL"
        )
        completeness_insufficient_count = sum(
            1 for r in results if r.completeness_grade == "INSUFFICIENT"
        )

        completeness_complete_rate = completeness_complete_count / total_count if total_count > 0 else 0.0
        completeness_partial_rate = completeness_partial_count / total_count if total_count > 0 else 0.0
        completeness_insufficient_rate = completeness_insufficient_count / total_count if total_count > 0 else 0.0

        # Accuracy with complete context
        complete_context_results = [r for r in results if r.completeness_grade == "COMPLETE"]
        correct_with_complete_context = sum(1 for r in complete_context_results if r.grade)
        total_with_complete_context = len(complete_context_results)
        accuracy_with_complete_context = (
            correct_with_complete_context / total_with_complete_context
            if total_with_complete_context > 0
            else None
        )

        # Latency stats
        retrieval_durations = [r.retrieval_duration for r in results]
        response_durations = [r.response_duration for r in results]
        total_durations = [r.total_duration for r in results]

        retrieval_stats = self._calculate_latency_stats(retrieval_durations)
        response_stats = self._calculate_latency_stats(response_durations)
        total_stats = self._calculate_latency_stats(total_durations)

        # Context stats
        context_tokens = [r.context_tokens for r in results]
        context_chars = [r.context_chars for r in results]

        token_stats = self._calculate_token_stats(context_tokens)
        char_stats = self._calculate_token_stats(context_chars)

        # Category breakdown
        by_category = self._calculate_category_metrics(results)

        # Difficulty breakdown
        by_difficulty = self._calculate_difficulty_metrics(results)

        return BenchmarkMetrics(
            accuracy=accuracy,
            correct_count=correct_count,
            total_count=total_count,
            completeness_complete_rate=completeness_complete_rate,
            completeness_complete_count=completeness_complete_count,
            completeness_partial_rate=completeness_partial_rate,
            completeness_partial_count=completeness_partial_count,
            completeness_insufficient_rate=completeness_insufficient_rate,
            completeness_insufficient_count=completeness_insufficient_count,
            accuracy_with_complete_context=accuracy_with_complete_context,
            correct_with_complete_context=correct_with_complete_context,
            total_with_complete_context=total_with_complete_context,
            retrieval_duration_stats=retrieval_stats,
            response_duration_stats=response_stats,
            total_duration_stats=total_stats,
            context_token_stats=token_stats,
            context_char_stats=char_stats,
            by_category=by_category,
            by_difficulty=by_difficulty,
        )

    def _calculate_latency_stats(self, durations: list[float]) -> LatencyStats:
        """Calculate latency statistics."""
        if not durations:
            return LatencyStats(
                median=0.0,
                mean=0.0,
                std_dev=0.0,
                p50=0.0,
                p90=0.0,
                p95=0.0,
                p99=0.0,
                min=0.0,
                max=0.0,
            )

        sorted_durations = sorted(durations)
        n = len(sorted_durations)

        return LatencyStats(
            median=median(durations),
            mean=mean(durations),
            std_dev=stdev(durations) if n > 1 else 0.0,
            p50=sorted_durations[int(n * 0.50)],
            p90=sorted_durations[int(n * 0.90)],
            p95=sorted_durations[int(n * 0.95)],
            p99=sorted_durations[int(n * 0.99)],
            min=min(durations),
            max=max(durations),
        )

    def _calculate_token_stats(self, tokens: list[int]) -> TokenStats:
        """Calculate token statistics."""
        if not tokens:
            return TokenStats(median=0.0, mean=0.0, p95=0.0, p99=0.0, min=0.0, max=0.0)

        sorted_tokens = sorted(tokens)
        n = len(sorted_tokens)

        return TokenStats(
            median=median(tokens),
            mean=mean(tokens),
            p95=sorted_tokens[int(n * 0.95)],
            p99=sorted_tokens[int(n * 0.99)],
            min=min(tokens),
            max=max(tokens),
        )

    def _calculate_category_metrics(self, results: list[EvaluationResult]) -> list[CategoryMetrics]:
        """Calculate metrics grouped by category."""
        by_category: dict[str, list[EvaluationResult]] = defaultdict(list)
        for r in results:
            by_category[r.category].append(r)

        category_metrics = []
        for category, cat_results in sorted(by_category.items()):
            correct = sum(1 for r in cat_results if r.grade)
            total = len(cat_results)
            accuracy = correct / total if total > 0 else 0.0

            avg_retrieval = mean([r.retrieval_duration for r in cat_results])
            avg_response = mean([r.response_duration for r in cat_results])

            category_metrics.append(
                CategoryMetrics(
                    category=category,
                    accuracy=accuracy,
                    correct_count=correct,
                    total_count=total,
                    avg_retrieval_duration=avg_retrieval,
                    avg_response_duration=avg_response,
                )
            )

        return category_metrics

    def _calculate_difficulty_metrics(
        self, results: list[EvaluationResult]
    ) -> list[DifficultyMetrics]:
        """Calculate metrics grouped by difficulty."""
        by_difficulty: dict[str, list[EvaluationResult]] = defaultdict(list)
        for r in results:
            by_difficulty[r.difficulty].append(r)

        difficulty_metrics = []
        for difficulty, diff_results in sorted(by_difficulty.items()):
            correct = sum(1 for r in diff_results if r.grade)
            total = len(diff_results)
            accuracy = correct / total if total > 0 else 0.0

            difficulty_metrics.append(
                DifficultyMetrics(
                    difficulty=difficulty,
                    accuracy=accuracy,
                    correct_count=correct,
                    total_count=total,
                )
            )

        return difficulty_metrics

    def load_run(self, run_id: str) -> dict:
        """Load results from a previous run."""
        run_dir = self.experiments_dir / run_id
        results_path = run_dir / "results.json"

        if not results_path.exists():
            raise FileNotFoundError(f"Results not found: {results_path}")

        with open(results_path) as f:
            return json.load(f)

    def save_experiment_summary(
        self,
        experiment_dir: Path,
        all_run_metrics: list[BenchmarkMetrics],
        all_run_results: list[EvaluationResult],
    ) -> None:
        """Save aggregated summary statistics across multiple runs.

        Implements hybrid approach:
        1. Unified distribution from all raw values (shows true outliers)
        2. Per-run summary stats (shows consistency across runs)
        """
        if not all_run_metrics:
            self.logger.warning("No run metrics to aggregate")
            return

        # Extract per-run aggregate values
        accuracies = [m.accuracy for m in all_run_metrics]
        complete_rates = [m.completeness_complete_rate for m in all_run_metrics]
        partial_rates = [m.completeness_partial_rate for m in all_run_metrics]
        insufficient_rates = [m.completeness_insufficient_rate for m in all_run_metrics]

        # Extract all raw values from all runs for unified distribution
        all_retrieval_durations = [r.retrieval_duration for r in all_run_results]
        all_response_durations = [r.response_duration for r in all_run_results]
        all_context_tokens = [r.context_tokens for r in all_run_results]

        # Extract per-run distribution statistics
        per_run_retrieval_medians = [m.retrieval_duration_stats.median for m in all_run_metrics]
        per_run_retrieval_p95s = [m.retrieval_duration_stats.p95 for m in all_run_metrics]
        per_run_retrieval_p99s = [m.retrieval_duration_stats.p99 for m in all_run_metrics]

        per_run_response_medians = [m.response_duration_stats.median for m in all_run_metrics]
        per_run_response_p95s = [m.response_duration_stats.p95 for m in all_run_metrics]
        per_run_response_p99s = [m.response_duration_stats.p99 for m in all_run_metrics]

        per_run_token_medians = [m.context_token_stats.median for m in all_run_metrics]
        per_run_token_p95s = [m.context_token_stats.p95 for m in all_run_metrics]
        per_run_token_p99s = [m.context_token_stats.p99 for m in all_run_metrics]

        # Helper function to calculate statistics
        def calc_stats(values: list[float]) -> dict:
            n = len(values)
            if n == 0:
                return {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0, "std_dev": 0.0, "runs": []}

            return {
                "mean": mean(values),
                "median": median(values),
                "min": min(values),
                "max": max(values),
                "std_dev": stdev(values) if n > 1 else 0.0,
                "runs": values,
            }

        # Helper function to calculate distribution stats (for unified distributions)
        def calc_distribution_stats(values: list[float]) -> dict:
            if not values:
                return {
                    "median": 0.0,
                    "mean": 0.0,
                    "p95": 0.0,
                    "p99": 0.0,
                    "min": 0.0,
                    "max": 0.0,
                    "std_dev": 0.0,
                }

            sorted_values = sorted(values)
            n = len(sorted_values)

            return {
                "median": median(values),
                "mean": mean(values),
                "p95": sorted_values[int(n * 0.95)],
                "p99": sorted_values[int(n * 0.99)],
                "min": min(values),
                "max": max(values),
                "std_dev": stdev(values) if n > 1 else 0.0,
            }

        # Build experiment summary
        timestamp = experiment_dir.name.replace("experiment_", "")
        summary = {
            "experiment_id": experiment_dir.name,
            "timestamp": timestamp,
            "num_runs": len(all_run_metrics),
            "dataset": "locomo",
            "config": self.config.model_dump(),
            "aggregated_metrics": {
                "accuracy": calc_stats(accuracies),
                "completeness_complete_rate": calc_stats(complete_rates),
                "completeness_partial_rate": calc_stats(partial_rates),
                "completeness_insufficient_rate": calc_stats(insufficient_rates),
                # Hybrid approach for latency: unified distribution + per-run stats
                "retrieval_latency_seconds": {
                    "all_runs_combined": calc_distribution_stats(all_retrieval_durations),
                    "per_run_medians": calc_stats(per_run_retrieval_medians),
                    "per_run_p95s": calc_stats(per_run_retrieval_p95s),
                    "per_run_p99s": calc_stats(per_run_retrieval_p99s),
                },
                "response_latency_seconds": {
                    "all_runs_combined": calc_distribution_stats(all_response_durations),
                    "per_run_medians": calc_stats(per_run_response_medians),
                    "per_run_p95s": calc_stats(per_run_response_p95s),
                    "per_run_p99s": calc_stats(per_run_response_p99s),
                },
                # Hybrid approach for tokens: unified distribution + per-run stats
                "context_tokens": {
                    "all_runs_combined": calc_distribution_stats(all_context_tokens),
                    "per_run_medians": calc_stats(per_run_token_medians),
                    "per_run_p95s": calc_stats(per_run_token_p95s),
                    "per_run_p99s": calc_stats(per_run_token_p99s),
                },
            },
            "run_files": [f"run_{i+1}_results.json" for i in range(len(all_run_metrics))],
        }

        # Save to experiment directory using orjson for fast serialization
        summary_path = experiment_dir / "experiment_summary.json"
        with open(summary_path, "wb") as f:
            f.write(orjson.dumps(summary, option=orjson.OPT_INDENT_2))

        self.logger.info(f"Saved experiment summary to {summary_path}")
        self.logger.info(f"Experiment mean accuracy: {summary['aggregated_metrics']['accuracy']['mean']:.3f}")
        self.logger.info(f"Experiment std dev: {summary['aggregated_metrics']['accuracy']['std_dev']:.3f}")
        self.logger.info(
            f"Combined retrieval p99: {summary['aggregated_metrics']['retrieval_latency_seconds']['all_runs_combined']['p99']:.3f}s"
        )

    def list_runs(self) -> list[str]:
        """List all available run IDs."""
        runs = []
        for run_dir in self.experiments_dir.glob("run_*"):
            if run_dir.is_dir():
                runs.append(run_dir.name)
        return sorted(runs, reverse=True)
