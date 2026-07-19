from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).parents[1]
TEST_ROOT = WORKSPACE / "tests"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from run_accuracy_benchmark import entity_key


class AccuracyBenchmarkTests(unittest.TestCase):
    def test_interface_package_is_part_of_accuracy_key(self):
        standard = entity_key("publishers", {"package": "probe", "name": "status", "type": "std_msgs/msg/String"})
        custom = entity_key("publishers", {"package": "probe", "name": "status", "type": "custom_msgs/msg/String"})

        self.assertNotEqual(standard, custom)

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


if __name__ == "__main__":
    unittest.main()
