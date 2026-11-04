import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from final_report import (
    LIFECYCLE,
    OLD,
    bootstrap_gain,
    difficulty_transfer_section,
    empty_response_sensitivity,
    holm,
    latency,
    paired_randomization_p,
    passed,
    pooled_gain,
    raw_visible_output,
    request_failures,
    skill_stats,
    teacher_file,
    teacher_transfer_audit,
    token_totals,
    validate_inode_resume,
    validate_recorder_recovery,
    validate_rows,
)


class StatisticsTests(unittest.TestCase):
    def test_teacher_file_has_portable_discovery_and_explicit_override(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "executors"
            explicit = Path(directory) / "elsewhere.jsonl"
            self.assertEqual(teacher_file(root, explicit), explicit)
            with self.assertRaises(ValueError):
                teacher_file(root)
            first = root.parent / "results" / "first" / "teacher.jsonl"
            first.parent.mkdir(parents=True)
            first.write_text("")
            self.assertEqual(teacher_file(root), first)
            second = root.parent / "results" / "second" / "teacher.jsonl"
            second.parent.mkdir()
            second.write_text("")
            with self.assertRaises(ValueError):
                teacher_file(root)

    def test_pool_keeps_cells_together_and_measures_width_and_correlation(self):
        result = pooled_gain([[0.25, -0.25], [-0.25, 0.25]], replicates=500)
        self.assertEqual(
            (result["gain"], result["ci_low"], result["ci_high"], result["ci_width"]), (0, 0, 0, 0)
        )
        self.assertEqual(result["mean_per_cell_ci_width"], 0.5)
        self.assertAlmostEqual(result["mean_pairwise_correlation"], -1)
        self.assertEqual(result["correlation_pairs"], 1)
        aligned = pooled_gain([[0, 0], [1, 1]], replicates=500)
        self.assertEqual((aligned["gain"], aligned["ci_low"], aligned["ci_high"]), (0.5, 0, 1))
        self.assertAlmostEqual(aligned["mean_pairwise_correlation"], 1)
        self.assertEqual(result, pooled_gain([[0.25, -0.25], [-0.25, 0.25]], replicates=500))

    def test_pool_empty_invalid_and_undefined_correlations(self):
        for values in ([], [1], [[], []], [[float("nan")]], [[float("inf")]]):
            with self.assertRaises(ValueError):
                pooled_gain(values)
        result = pooled_gain([[0.25], [0.25]], replicates=100)
        self.assertEqual(
            (result["gain"], result["ci_width"], result["mean_per_cell_ci_width"]), (0.25, 0, 0)
        )
        self.assertIsNone(result["mean_pairwise_correlation"])
        result = pooled_gain([[0, 0.25], [1, 0.25]], replicates=100)
        self.assertEqual(result["undefined_correlation_pairs"], 1)

    def transfer_fixture(self):
        teacher = [
            {
                "domain": "test",
                "task_index": task,
                "trial": trial,
                "trajectory_uuid": f"{task}-{trial}",
                "succeeded": bool(task),
            }
            for task in (0, 1)
            for trial in range(1, 5)
        ]
        skill = {
            "full_markdown": "<!-- zep-evidence: good -->",
            "cited_trace_uuids": ["1-1"],
            "source_trace_uuids": ["0-1", "1-1"],
            "lineage": {
                "evidence": [
                    {"summary_version_uuid": "good", "trace_uuid": "1-1"},
                    {"summary_version_uuid": "uncited", "trace_uuid": "0-1"},
                ]
            },
        }
        return teacher, skill

    def test_transfer_audit_distinguishes_citations_from_failed_source_lineage(self):
        teacher, skill = self.transfer_fixture()
        result = teacher_transfer_audit(teacher, [skill])
        self.assertEqual(
            (
                result["teacher_zero_tasks"],
                result["teacher_zero_trials"],
                result["zero_task_citations"],
            ),
            (1, 4, 0),
        )
        self.assertEqual(
            (result["zero_task_source_traces"], result["skills_with_zero_task_lineage"]), (1, 1)
        )
        self.assertEqual(result["tasks"][0]["skills_citing"], 0)
        self.assertEqual(teacher_transfer_audit(teacher, [])["source_teacher_traces"], 0)
        self.assertEqual(teacher_transfer_audit([], [])["teacher_zero_tasks"], 0)
        for invalid in (
            dict(skill, cited_trace_uuids=[]),
            dict(skill, source_trace_uuids=["unknown"]),
            dict(skill, full_markdown="<!-- zep-evidence: missing -->"),
            dict(skill, full_markdown="<!-- zep-evidence: uncited -->", cited_trace_uuids=["0-1"]),
        ):
            with self.assertRaises(ValueError):
                teacher_transfer_audit(teacher, [invalid])
        for invalid in (teacher[:-1], teacher + teacher[:1]):
            with self.assertRaises(ValueError):
                teacher_transfer_audit(invalid, [skill])

    def test_difficulty_text_reports_exceptions_and_lineage_limit(self):
        teacher, skill = self.transfer_fixture()

        def estimate(value):
            return {"gain": value, "ci_low": value - 0.01, "ci_high": value + 0.01}

        gains = {
            ("model", "low"): {
                "all": estimate(0.02),
                "some": estimate(0.03),
                "none": estimate(0.01),
            },
            ("model", "max"): {
                "all": estimate(0.02),
                "some": estimate(0.01),
                "none": estimate(0.03),
            },
        }
        text = difficulty_transfer_section(
            gains, estimate(0.01), teacher_transfer_audit(teacher, [skill])
        )
        self.assertIn("strictly highest measured gain in 1 of 2 cells", text)
        self.assertIn("The 1–3 gain is lower than the 4/4 gain", text)
        self.assertIn("does not establish that any gain came only", text)
        self.assertIn("CI includes 0", text)

    def test_inode_resume_preserves_results_and_requires_the_new_commit(self):
        original = {"domain": "test", "task_index": 0, "trial": 1, "git_sha": OLD}
        resumed = {"domain": "test", "task_index": 1, "trial": 1, "git_sha": LIFECYCLE}
        episode = ["test", 0, 1]
        sha = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
        plan = {
            "configurations": {
                "a": {"preserved_row_hashes": [[episode, sha]], "to_run_keys": [["test", 1, 1]]}
            }
        }
        self.assertEqual(validate_inode_resume(plan, {"a": [original, resumed]}, []), 1)
        self.assertEqual(validate_inode_resume({"configurations": {}}, {}, []), 0)
        for rows in (
            [original],
            [original, dict(resumed, git_sha=OLD)],
            [dict(original, error="changed"), resumed],
        ):
            with self.assertRaises(ValueError):
                validate_inode_resume(plan, {"a": rows}, [])
        self.assertEqual(
            validate_inode_resume(
                plan,
                {"a": [dict(original, git_sha=LIFECYCLE), resumed]},
                [{"config": "a", "episode_key": episode}],
            ),
            0,
        )

    def test_recorder_recovery_normal_empty_and_invalid(self):
        entry = {
            "id": "a",
            "classification": "harness_recording_failure",
            "config": "test",
            "episode_key": ["test", 1, 2],
            "seed": 373753,
        }
        result = {"ledger_id": "a", "config": "test", "episode_key": ["test", 1, 2], "seed": 373753}
        validate_recorder_recovery([], [])
        validate_recorder_recovery([entry], [result])
        for results in (
            [],
            [result, result],
            [dict(result, seed=300)],
            [dict(result, ledger_id="b")],
        ):
            with self.assertRaises(ValueError):
                validate_recorder_recovery([entry], results)
        with self.assertRaises(ValueError):
            validate_recorder_recovery(
                [dict(entry, classification="model_provider_error")], [result]
            )

    def test_request_failures_normal_empty_and_invalid(self):
        self.assertEqual(request_failures([]), [])
        self.assertEqual(
            request_failures(
                [{"service": "zep", "outcome": "429"}] * 2
                + [{"service": "openai", "outcome": "ReadTimeout"}]
            ),
            [
                {"service": "openai", "outcome": "ReadTimeout", "count": 1},
                {"service": "zep", "outcome": "429", "count": 2},
            ],
        )
        with self.assertRaises(KeyError):
            request_failures([{"service": "openai"}])

    def sensitivity_rows(self):
        return [
            {"domain": "test", "task_index": task, "trial": trial, "succeeded": False}
            for task in (0, 1)
            for trial in range(1, 5)
        ]

    def test_sensitivity_drops_pair_from_both_arms_and_weights_episodes(self):
        a = self.sensitivity_rows()
        b = [dict(row, succeeded=row["task_index"] == 0) for row in a]
        a[0]["empty_response"] = True
        result = empty_response_sensitivity(a, b, replicates=500)
        self.assertEqual(result["retained_pairs"], 7)
        self.assertEqual(result["passes"], {"none": 0, "skills": 3})
        self.assertEqual(result["gain"], 3 / 7)
        self.assertEqual(result["tasks"], 2)
        self.assertEqual((result["ci_low"], result["ci_high"]), (0, 1))
        b[1]["empty_response"] = True
        a[1]["succeeded"] = True
        result = empty_response_sensitivity(a, b, replicates=50)
        self.assertEqual(result["retained_pairs"], 6)
        self.assertEqual(result["passes"], {"none": 0, "skills": 2})

    def test_sensitivity_empty_and_mismatched_inputs(self):
        self.assertIsNone(empty_response_sensitivity([], [])["gain"])
        rows = self.sensitivity_rows()
        all_empty = [dict(row, empty_response=True) for row in rows]
        result = empty_response_sensitivity(rows, all_empty)
        self.assertEqual(result["dropped_pairs"], 8)
        self.assertIsNone(result["pass_rates"]["skills"])
        for a, b in [(rows, rows[:-1]), (rows + rows[:1], rows)]:
            with self.assertRaises(ValueError):
                empty_response_sensitivity(a, b)

    def test_sensitivity_keeps_other_errors_as_failures(self):
        a = self.sensitivity_rows()
        b = [dict(row, succeeded=True) for row in a]
        b[0]["error"] = "TimeoutError"
        result = empty_response_sensitivity(a, b, replicates=50)
        self.assertEqual(result["passes"]["skills"], 7)
        self.assertEqual(result["retained_pairs"], 8)
        self.assertEqual(result["gain"], 7 / 8)

    def test_identical_arms(self):
        a = np.array([[1, 0, 1, 0], [0, 1, 0, 1]])
        self.assertEqual(
            bootstrap_gain(a, a, replicates=500), {"gain": 0, "ci_low": 0, "ci_high": 0}
        )
        self.assertEqual(paired_randomization_p(a, a), 1)

    def test_four_trials_stay_together(self):
        a = np.zeros((2, 4))
        b = np.array([[1, 1, 1, 1], [0, 0, 0, 0]])
        result = bootstrap_gain(a, b, replicates=2000)
        self.assertEqual(result, {"gain": 0.5, "ci_low": 0, "ci_high": 1})

    def test_exact_paired_test_and_direction(self):
        a, b = np.zeros((4, 4)), np.ones((4, 4))
        self.assertEqual(paired_randomization_p(a, b), 0.125)
        self.assertEqual(paired_randomization_p(b, a), 0.125)
        self.assertEqual(bootstrap_gain(b, a, replicates=50)["gain"], -1)

    def test_holm_preserves_order_and_monotonicity(self):
        self.assertEqual(holm([0.04, 0.001, 0.02]), [0.04, 0.003, 0.04])
        self.assertEqual(holm([0.8, 0.8]), [1, 1])

    def test_monte_carlo_p_is_reproducible_and_nonzero(self):
        a, b = np.zeros((17, 4)), np.ones((17, 4))
        p = paired_randomization_p(a, b, seed=300, replicates=500)
        self.assertEqual(p, paired_randomization_p(a, b, seed=300, replicates=500))
        self.assertGreaterEqual(p, 1 / 501)
        self.assertLess(p, 0.05)

    def test_empty_and_mismatched_inputs(self):
        for a, b in [
            (np.zeros((0, 4)), np.zeros((0, 4))),
            (np.zeros((1, 4)), np.zeros((2, 4))),
            ([1], [1]),
        ]:
            with self.assertRaises(ValueError):
                bootstrap_gain(a, b)
            with self.assertRaises(ValueError):
                paired_randomization_p(a, b)
        with self.assertRaises(ValueError):
            holm([])


class AuditTests(unittest.TestCase):
    def test_raw_output_ignores_reasoning_and_detects_text_and_tools(self):
        self.assertEqual(
            raw_visible_output(
                [
                    {
                        "output": [
                            {"type": "reasoning", "summary": [{"text": "thought"}]},
                            {"type": "message", "content": [{"type": "output_text", "text": ""}]},
                        ]
                    }
                ]
            ),
            {"text_chars": 0, "tool_calls": 0},
        )
        self.assertEqual(
            raw_visible_output(
                [
                    {"type": "content_block_start", "content_block": {"type": "tool_use"}},
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "answer"},
                    },
                ]
            ),
            {"text_chars": 6, "tool_calls": 1},
        )
        self.assertEqual(raw_visible_output([]), {"text_chars": 0, "tool_calls": 0})

    def test_empty_responses_are_failed(self):
        self.assertFalse(passed({"succeeded": True, "empty_response": True}))
        self.assertFalse(passed({"succeeded": True, "error": "error"}))

    def test_validation_rejects_partial_and_duplicate_rows(self):
        row = {
            "domain": "test",
            "task_index": 1,
            "trial": 1,
            "succeeded": False,
            "reward": 0,
            "git_sha": OLD,
        }
        expected = {("test", 1, 1)}
        validate_rows([row], expected)
        for rows in [[], [row, row]]:
            with self.assertRaises(ValueError):
                validate_rows(rows, expected)

    def test_skill_denominators_and_clock_requestor(self):
        read = {"found": False, "repaired": False}
        row = {
            "skill_read_calls": [read],
            "skill_read_positions": [{"tool_call_index": 2}],
            "skill_invalid_loads": 1,
            "tool_call_sequence": [
                {"tool": "get_current_time", "requestor": "user", "tool_call_index": 1},
                {"tool": "read_skill", "requestor": "assistant", "tool_call_index": 2},
                {"tool": "shell", "requestor": "assistant", "tool_call_index": 3},
            ],
        }
        s = skill_stats([row, {}])
        self.assertEqual(
            (s["reads"], s["with_read"], s["missing"], s["invalid"], s["shell"], s["clock"]),
            (1, 1, 1, 1, 1, 0),
        )
        self.assertEqual(s["reads_per_episode"], 0.5)
        self.assertIsNone(skill_stats([])["reads_per_episode"])

    def test_tokens_do_not_double_count_subsets(self):
        rows = [
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "cached_tokens": 80,
                "cache_creation_tokens": 20,
                "reasoning_tokens": None,
                "succeeded": True,
            },
            {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "reasoning_tokens": None,
                "succeeded": False,
            },
        ]
        totals = token_totals(rows)
        self.assertEqual(totals["total_tokens"], 165)
        self.assertEqual(totals["tokens_per_passed_episode"], 165)
        self.assertEqual(totals["mean_tokens_in_successful_episode"], 110)
        self.assertIsNone(totals["reasoning_tokens"])
        self.assertIsNone(token_totals([])["tokens_per_passed_episode"])

    def test_latency_empty_and_normal(self):
        self.assertIsNone(latency([])["mean"])
        self.assertEqual(latency([1, 2, 3])["mean"], 2)


if __name__ == "__main__":
    unittest.main()
