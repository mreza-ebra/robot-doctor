from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE = Path(__file__).parents[1]
TEST_ROOT = WORKSPACE / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import run_accuracy_benchmark as benchmark_runner

from run_accuracy_benchmark import entity_key


class AccuracyBenchmarkTests(unittest.TestCase):
    def test_interface_package_is_part_of_accuracy_key(self):
        standard = entity_key("publishers", {"package": "probe", "name": "status", "type": "std_msgs/msg/String"})
        custom = entity_key("publishers", {"package": "probe", "name": "status", "type": "custom_msgs/msg/String"})

        self.assertNotEqual(standard, custom)

    def test_ground_truth_breadth_and_label_count(self):
        manifest = json.loads((WORKSPACE / "tests" / "ground_truth" / "manifest.json").read_text(encoding="utf-8"))
        names = {item["name"] for item in manifest["benchmarks"]}
        labels = sum(
            sum(len(items) for items in benchmark.get("entities", {}).values())
            + sum(len(items) for items in benchmark.get("launch_graph", {}).values())
            + len(benchmark.get("diagnostics", []))
            for benchmark in manifest["benchmarks"]
        )

        self.assertEqual(manifest["schema_version"], "1.1")
        self.assertEqual(labels, 121)
        self.assertTrue(
            {"moveit2_servo_source", "ros2_control_manager_source", "launch_fixture", "diagnostic_fixture"} <= names
        )

    def test_required_manual_ground_truth_meets_thresholds(self):
        result = subprocess.run(
            [sys.executable, str(WORKSPACE / "tests" / "run_accuracy_benchmark.py")],
            cwd=WORKSPACE,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS python_fixture", result.stdout)
        self.assertIn("PASS cpp_fixture", result.stdout)
        self.assertIn("PASS launch_fixture", result.stdout)
        self.assertIn("PASS diagnostic_fixture", result.stdout)

    def test_revision_mismatch_fails_a_pinned_benchmark(self):
        benchmark = {
            "name": "pinned_fixture",
            "revision": "expected-revision",
            "minimum_precision": 1.0,
            "minimum_recall": 1.0,
            "entities": {},
        }
        with mock.patch.object(benchmark_runner, "git_revision", return_value="different-revision"):
            result = benchmark_runner.score_benchmark(benchmark, WORKSPACE / "tests" / "fixtures" / "python_robot")

        self.assertFalse(result["passed"])
        self.assertFalse(result["revision_matches"])


if __name__ == "__main__":
    unittest.main()
