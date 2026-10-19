from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from robot_doctor.config import ScanConfig
from robot_doctor.public_case import CASES, REVISION, case_assets, case_directory, is_target
from robot_doctor.scanner import launch_package_share_references, scan_repository
from robot_doctor.web import home_page, public_case_body, public_cases_body


class PublicCaseTests(unittest.TestCase):
    def make_package(self, root: Path, source: str, *, scope: str = "launch") -> Path:
        package = root / "consumer"
        (package / scope).mkdir(parents=True)
        (package / "package.xml").write_text('<package format="3"><name>consumer</name><version>0.1.0</version><description>Fixture</description><maintainer email="fixture@example.com">Fixture</maintainer><license>MIT</license><buildtool_depend>ament_cmake</buildtool_depend><export><build_type>ament_cmake</build_type></export></package>')
        (package / "CMakeLists.txt").write_text('cmake_minimum_required(VERSION 3.8)\nproject(consumer)\nfind_package(ament_cmake REQUIRED)\nament_package()\n')
        launch = package / scope / "entry.launch.py"
        launch.write_text(source)
        return launch

    def test_literal_share_lookups_with_aliases_are_dependencies(self):
        source = '''from launch_ros.substitutions import FindPackageShare as Share
from ament_index_python.packages import get_package_share_directory as share_dir
import ament_index_python.packages as index

def generate_launch_description():
    return [Share("vendor_backend"), share_dir(package_name="second_backend"),
            index.get_package_share_path("third_backend")]
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_package(root, source)
            data = scan_repository(root)
            findings = [item for item in data["diagnostics"] if item["code"] == "RD101"]
            self.assertEqual({item["dependency"] for item in findings}, {"vendor_backend", "second_backend", "third_backend"})
            self.assertTrue(all(item["severity"] == "warning" and item["direct_reference"] for item in findings))
            self.assertTrue(all(item["evidence"][0]["extractor"] == "python_launch_package_share" for item in findings))
            self.assertTrue(all(item["remediation"]["suggested_files"][0] == "consumer/package.xml" for item in findings))
            self.assertTrue(all("<exec_depend>" in item["remediation"]["patch_hint"] for item in findings))
            self.assertFalse(any(item["code"] == "RD101" for item in scan_repository(root, config=ScanConfig(dependency_mode="off"))["diagnostics"]))
            manifest = root / "consumer/package.xml"
            manifest.write_text(manifest.read_text().replace("</package>", '<exec_depend>vendor_backend</exec_depend><exec_depend>second_backend</exec_depend><exec_depend>third_backend</exec_depend></package>'))
            self.assertFalse(any(item["code"] == "RD101" for item in scan_repository(root)["diagnostics"]))

    def test_dynamic_unrelated_and_shadowed_calls_do_not_become_dependencies(self):
        variants = (
            'from launch_ros.substitutions import FindPackageShare\nfrom launch.substitutions import LaunchConfiguration\nx=FindPackageShare(LaunchConfiguration("package"))\n',
            'def FindPackageShare(name):\n return name\nx=FindPackageShare("not_a_ros_lookup")\n',
            'from ament_index_python.packages import get_package_share_directory\ndef helper(get_package_share_directory):\n return get_package_share_directory("shadowed")\n',
            'from launch_ros.substitutions import FindPackageShare\nFindPackageShare = lambda name: name\nx=FindPackageShare("reassigned")\n',
            'from launch_ros.substitutions import FindPackageShare\nx=FindPackageShare("$(var package)")\n',
        )
        for source in variants:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                launch = self.make_package(root, source)
                self.assertEqual(launch_package_share_references(launch, root), {})

    def test_test_only_lookup_does_not_generate_production_dependency_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_package(root, 'from launch_ros.substitutions import FindPackageShare\nx=FindPackageShare("vendor_backend")\n', scope="test")
            self.assertFalse(any(item["code"] == "RD101" for item in scan_repository(root)["diagnostics"]))

    def test_bundled_case_retains_real_provenance_and_verification_limits(self):
        assets = case_directory()
        case = json.loads((assets / "case.json").read_text())
        before = json.loads((assets / "before.json").read_text())
        after = json.loads((assets / "after.json").read_text())
        initial = json.loads((assets / "initial.json").read_text())
        self.assertEqual(case["revision"], REVISION)
        self.assertEqual(before["provenance"]["git"]["commit_sha"], REVISION)
        self.assertEqual(sum(map(is_target, before["diagnostics"])), 1)
        self.assertFalse(any(map(is_target, after["diagnostics"])))
        self.assertFalse(any(map(is_target, initial["diagnostics"])))
        self.assertFalse(case["checks"]["ros_runtime_run"])
        self.assertFalse(case["checks"]["upstream_submission"])
        body = public_case_body(case, "/case-study/")
        self.assertIn("issue-guided reproduction", body)
        self.assertIn("Other diagnostics remain", body)
        self.assertIn('/case-study/fix.patch', body)
        self.assertIn('href="/case-study"', home_page("test"))

    def test_patch_generator_refuses_an_already_corrected_manifest(self):
        spec = importlib.util.spec_from_file_location("public_case_runner", ROOT / "tools/run_public_case_study.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = '<package>\n  <exec_depend>fanuc_crx_description</exec_depend>\n</package>\n'
        corrected = module.corrected_manifest(source)
        self.assertEqual(len(corrected.splitlines()), len(source.splitlines()) + 1)
        with self.assertRaises(ValueError):
            module.corrected_manifest(corrected)

    def test_all_cases_have_distinct_provenance_and_only_target_removed(self):
        for case_id, details in CASES.items():
            with self.subTest(case=case_id):
                assets = case_directory(case_id)
                self.assertTrue(all((assets / name).is_file() for name in case_assets(case_id)))
                case, before, after = [json.loads((assets / name).read_text()) for name in ("case.json", "before.json", "after.json")]
                self.assertEqual(case["repository_url"], details["repository_url"])
                self.assertEqual(before["provenance"]["git"]["commit_sha"], details["revision"])
                self.assertEqual(sum(is_target(item, case_id) for item in before["diagnostics"]), 1)
                self.assertFalse(any(is_target(item, case_id) for item in after["diagnostics"]))
                signatures = lambda data: Counter(json.dumps(item, sort_keys=True) for item in data["diagnostics"] if not is_target(item, case_id))
                self.assertEqual(signatures(before), signatures(after))
                self.assertFalse(case["checks"]["build_run"])
                self.assertFalse(case["checks"]["ros_runtime_run"])
        self.assertEqual(len({details["repository_url"] for details in CASES.values()}), len(CASES))
        with self.assertRaises(ValueError):
            case_directory("../fanuc")
        with self.assertRaises(ValueError):
            case_assets("../fanuc")

    def test_case_chooser_and_moveit_explain_the_reviewed_alternative(self):
        hub = public_cases_body()
        for key in CASES:
            self.assertIn(f'/case-studies/{key}', hub)
        offline = public_cases_body({key: value["showcase_folder"] + "/index.html" for key, value in CASES.items()})
        self.assertIn('href="moveit_case/index.html"', offline)
        case = json.loads((case_directory("moveit") / "case.json").read_text())
        initial = json.loads((case_directory("moveit") / "initial.json").read_text())
        self.assertEqual(sum(is_target(item, "moveit") for item in initial["diagnostics"]), 1)
        body = public_case_body(case, "/case-studies/moveit/")
        self.assertIn("not an automatic repair", body)
        self.assertIn("Visual behavior has not been compared", body)
        self.assertIn("Other diagnostics remain", body)
        self.assertIn('/case-studies/moveit/fix.patch', body)
        self.assertTrue(case["checks"]["manifest_unchanged"])

    def test_moveit_patch_requires_both_installed_local_presets(self):
        spec = importlib.util.spec_from_file_location("moveit_case_runner", ROOT / "tools/run_moveit_case_study.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = '\n'.join((
            'rviz_base = os.path.join(get_package_share_directory("moveit2_tutorials"), "launch")',
            'rviz_full_config = os.path.join(rviz_base, "panda_moveit_config_demo.rviz")',
            'rviz_empty_config = os.path.join(rviz_base, "panda_moveit_config_demo_empty.rviz")',
        ))
        corrected = module.corrected_launch(source)
        self.assertNotIn("moveit2_tutorials", corrected)
        with self.assertRaises(ValueError):
            module.corrected_launch(corrected)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "panda_moveit_config"
            (package / "launch").mkdir(parents=True)
            (package / "CMakeLists.txt").write_text('install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})')
            for name in module.RVIZ_FILES:
                (package / "launch" / name).write_text("Panels: []\n")
            self.assertEqual(len(module.check_local_rviz_paths(root, corrected)), 2)
            (package / "launch/moveit_empty.rviz").unlink()
            with self.assertRaises(ValueError):
                module.check_local_rviz_paths(root, corrected)

    def test_launch_context_requirement_is_informational_and_actionable(self):
        source = 'from launch.substitutions import LaunchConfiguration as Config\nx=Config("target_frame")\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_package(root, source)
            findings = [item for item in scan_repository(root)["diagnostics"] if item["code"] == "RD304"]
            self.assertEqual(len(findings), 1)
            finding = findings[0]
            self.assertEqual(finding["severity"], "info")
            self.assertEqual(finding["launch_configuration"], "target_frame")
            self.assertEqual(finding["package"], "consumer")
            self.assertIn("A parent launch", finding["message"])
            self.assertEqual(finding["remediation"]["commands"], ["ros2 launch consumer entry.launch.py --show-args"])
            self.assertIn("target_frame", finding["remediation"]["patch_hint"])
            self.assertEqual(finding["evidence"][0]["extractor"], "python_launch_configuration")

    def test_launch_context_defaults_declarations_and_assignments(self):
        variants = (
            'from launch.substitutions import LaunchConfiguration\nx=LaunchConfiguration("frame", default="")',
            'from launch.substitutions import LaunchConfiguration\nfrom launch.actions import DeclareLaunchArgument\nx=LaunchConfiguration("frame")\ny=DeclareLaunchArgument("frame")',
            'import launch.substitutions as subs\nfrom launch.actions import SetLaunchConfiguration as Set\nx=subs.LaunchConfiguration("frame")\ny=Set("frame", "map")',
            'from launch.substitutions import LaunchConfiguration\nfrom launch.actions import DeclareLaunchArgument\nx=LaunchConfiguration("frame")\ny=DeclareLaunchArgument(dynamic_name)',
        )
        for source in variants:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_package(root, source)
                self.assertFalse(any(item["code"] == "RD304" for item in scan_repository(root)["diagnostics"]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_package(root, 'import launch.substitutions as subs\nx=subs.LaunchConfiguration("frame", default=None)')
            self.assertTrue(any(item["code"] == "RD304" for item in scan_repository(root)["diagnostics"]))

    def test_launch_context_unknown_and_shadowed_calls_are_not_asserted(self):
        variants = (
            'def LaunchConfiguration(name): return name\nx=LaunchConfiguration("frame")',
            'from launch.substitutions import LaunchConfiguration\ndef helper(LaunchConfiguration): return LaunchConfiguration("frame")',
            'from launch.substitutions import LaunchConfiguration\nx=LaunchConfiguration(dynamic_name)',
            'from launch.substitutions import LaunchConfiguration\nx=LaunchConfiguration("frame", **options)',
            'from launch.substitutions import LaunchConfiguration\nLaunchConfiguration=lambda name:name\nx=LaunchConfiguration("frame")',
        )
        for source in variants:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_package(root, source)
                self.assertFalse(any(item["code"] == "RD304" for item in scan_repository(root)["diagnostics"]))

    def test_launch_context_requirement_obeys_existing_policy_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_package(root, 'from launch.substitutions import LaunchConfiguration\nx=LaunchConfiguration("frame")')
            suppressed = scan_repository(root, config=ScanConfig(suppress_diagnostics=frozenset({"RD304"})))
            self.assertFalse(any(item["code"] == "RD304" for item in suppressed["diagnostics"]))
            raised = scan_repository(root, config=ScanConfig(severity_overrides={"RD304": "warning"}))
            self.assertEqual([item["severity"] for item in raised["diagnostics"] if item["code"] == "RD304"], ["warning"])

    def test_turtlebot3_snapshot_discloses_initial_miss_and_existing_upstream_fix(self):
        assets = case_directory("turtlebot3")
        case = json.loads((assets / "case.json").read_text())
        initial = json.loads((assets / "initial.json").read_text())
        self.assertFalse(any(is_target(item, "turtlebot3") for item in initial["diagnostics"]))
        self.assertEqual(case["source_finding"]["severity"], "info")
        self.assertIsNone(case["adoption"]["clone_count"])
        self.assertTrue(case["checks"]["named_override_binding_preserved"])
        body = public_case_body(case)
        self.assertIn("Existing upstream proposed fix #1150", body)
        self.assertIn("original detector", body)
        self.assertIn("including an unrelated error", body)
        self.assertIn("still required", body)
        self.assertIn('class="finding finding-info" open', body)

    def test_turtlebot3_patch_preserves_named_override_binding(self):
        spec = importlib.util.spec_from_file_location("turtlebot3_case_runner", ROOT / "tools/run_turtlebot3_case_study.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = "def generate_launch_description():\n    namespace = LaunchConfiguration('namespace')\n    return LaunchDescription([\n    ])\n"
        corrected = module.corrected_launch(source)
        module.check_namespace_contract(corrected)
        with self.assertRaises(ValueError):
            module.corrected_launch(corrected)
        with self.assertRaises(ValueError):
            module.check_namespace_contract(corrected.replace("namespace = LaunchConfiguration", "wrong = LaunchConfiguration"))


if __name__ == "__main__":
    unittest.main()
