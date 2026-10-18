from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from robot_doctor.config import ScanConfig
from robot_doctor.public_case import REVISION, case_directory, is_target
from robot_doctor.scanner import launch_package_share_references, scan_repository
from robot_doctor.web import home_page, public_case_body


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


if __name__ == "__main__":
    unittest.main()
