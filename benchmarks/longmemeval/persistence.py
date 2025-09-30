#!/usr/bin/env python3
"""
Results persistence for benchmark runs
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from common import BenchmarkMetrics, EvaluationResult
from config import BenchmarkConfig


class ResultsPersistence:
    """Handles saving benchmark results to disk"""

    def __init__(self, experiments_dir: str | Path = "experiments"):
        self.experiments_dir = Path(experiments_dir)

    def save_run(
        self,
        config: BenchmarkConfig,
        metrics: BenchmarkMetrics,
        results: list[EvaluationResult],
        config_file_path: str | Path,
    ) -> Path:
        """
        Save a complete benchmark run to a timestamped directory

        Args:
            config: Benchmark configuration used
            metrics: Aggregate metrics
            results: Individual evaluation results
            config_file_path: Path to original config file for snapshotting

        Returns:
            Path to the run directory
        """
        # Create timestamped run directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"run_{timestamp}"
        run_dir = self.experiments_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Prepare output data
        output_data = {
            "config": config.model_dump(),
            "metrics": metrics.model_dump(),
            "results": [r.model_dump() for r in results],
        }

        # Save results JSON
        results_path = run_dir / "results.json"
        with open(results_path, "w") as f:
            json.dump(output_data, f, indent=2, default=str)

        # Save config snapshot
        config_snapshot_path = run_dir / "config.yaml"
        shutil.copy(config_file_path, config_snapshot_path)

        return run_dir
