from __future__ import annotations

import json
import sys
import tempfile
import unittest
from importlib import resources
from pathlib import Path

WORKSPACE = Path(__file__).parents[1]
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor import overviews as generate_project_overviews
from robot_doctor import scanner as ros_repo_discover


FIXTURES = Path(__file__).parent / "fixtures"
TURTLEBOT4 = WORKSPACE / "turtlebot4"


class RobotDoctorScannerTests(unittest.TestCase):
    def test_python_ast_finds_common_ros_entities(self):
        data = ros_repo_discover.scan_repository(FIXTURES / "python_robot")
        self.assertEqual(data["package_count"], 1)
        report = data["packages"][0]

        self.assertEqual({item["name"] for item in report["node_names"]}, {"probe_node", "managed_probe", "keyword_node"})
        self.assertIn(("status", "std_msgs/msg/String"), {(item["name"], item["type"]) for item in report["publishers"]})
        self.assertIn("status", {item["name"] for item in report["subscriptions"]})
        self.assertIn("reset", {item["name"] for item in report["service_servers"]})
        self.assertIn("remote_reset", {item["name"] for item in report["service_clients"]})
        self.assertIn("compute", {item["name"] for item in report["action_servers"]})
        self.assertIn(10, {item.get("default") for item in report["declared_parameters"]})
        self.assertTrue(any(item.get("lifecycle") for item in report["node_names"]))
        self.assertEqual(report["interfaces"][0]["sections"][0]["fields"][0]["name"], "ready")

    def test_keyword_python_apis_and_modern_entry_points(self):
        data = ros_repo_discover.scan_repository(FIXTURES / "python_robot")
        report = data["packages"][0]

        self.assertIn("keyword_node", {item["name"] for item in report["node_names"]})
        self.assertIn(("keyword_status", "std_msgs/msg/String"), {(item["name"], item["type"]) for item in report["publishers"]})
        self.assertIn(("keyword_command", "std_msgs/msg/String"), {(item["name"], item["type"]) for item in report["subscriptions"]})
        self.assertIn("keyword_reset", {item["name"] for item in report["service_servers"]})
        self.assertIn("keyword_remote_reset", {item["name"] for item in report["service_clients"]})
        self.assertIn("keyword_compute", {item["name"] for item in report["action_servers"]})
        self.assertIn("keyword_remote_compute", {item["name"] for item in report["action_clients"]})
        self.assertIn("keyword_rate", {item["name"] for item in report["declared_parameters"]})
        self.assertEqual({"probe_node", "keyword_node", "cfg_probe"}, {item["name"] for item in report["executables"]})
        self.assertIn("keyword_status", {item["name"] for item in data["architecture"]["topics"]})

    def test_cpp_parser_finds_standard_entities_and_urdf(self):
        data = ros_repo_discover.scan_repository(FIXTURES / "cpp_robot")
        report = data["packages"][0]

        self.assertEqual(report["node_names"][0]["name"], "probe_cpp_node")
        self.assertEqual({item["name"] for item in report["publishers"]}, {"status", "wrapped_status"})
        wrapped_publisher = next(item for item in report["publishers"] if item["name"] == "wrapped_status")
        self.assertEqual(wrapped_publisher["wrapper"]["method"], "connect")
        self.assertEqual(report["subscriptions"][0]["name"], "imu")
        self.assertEqual(report["service_servers"][0]["name"], "calibrate")
        self.assertEqual({item["name"] for item in report["service_clients"]}, {"remote_calibrate", "wrapped_reset"})
        self.assertEqual(report["action_servers"][0]["name"], "sequence")
        self.assertEqual(report["action_clients"][0]["name"], "wrapped_sequence")
        self.assertEqual(report["action_clients"][0]["wrapper"]["class"], "ActionWrapper")
        self.assertEqual(data["architecture"]["tf"]["transforms"][0]["parent"], "base_link")
        self.assertTrue(any(item["name"] == "imu_sensor" for item in data["architecture"]["sensors"]))
        self.assertNotIn("RD104", {item["code"] for item in data["diagnostics"]})

    def test_launch_graph_covers_python_xml_yaml_and_composition(self):
        data = ros_repo_discover.scan_repository(FIXTURES / "launch_robot")
        launch_files = data["launch_graph"]["files"]

        self.assertEqual({item["format"] for item in launch_files}, {"python", "xml", "yaml"})
        self.assertTrue(all(edge["resolved"] for edge in data["launch_graph"]["edges"]))
        actions = [action for launch in launch_files for action in launch["actions"]]
        self.assertTrue(any(action.get("composed") for action in actions))
        self.assertTrue(any(action.get("namespace") == "sensors" for action in actions))
        self.assertTrue(any(action.get("remappings") for action in actions))
        self.assertTrue(any(launch["arguments"] for launch in launch_files))
        self.assertNotIn("RD301", {item["code"] for item in data["diagnostics"]})
        self.assertNotIn("RD302", {item["code"] for item in data["diagnostics"]})

    def test_node_graph_applies_namespaces_remaps_and_parameter_precedence(self):
        data = ros_repo_discover.scan_repository(FIXTURES)
        launched = next(item for item in data["architecture"]["nodes"] if item["origin"] == "launch" and item["name"] == "python_probe")

        self.assertEqual(launched["namespace"], "/robot/sensors")
        self.assertIn("/robot/sensors/robot_status", {item["name"] for item in launched["publishers"]})
        parameters = {item["name"]: item for item in launched["parameters"] if item["effective"]}
        self.assertEqual(parameters["global_enabled"]["value"], True)
        self.assertEqual(parameters["controller.gain"]["value"], 1.5)
        self.assertEqual(parameters["controller.gain"]["type"], "double")
        self.assertEqual(parameters["controller.limits.maximum"]["value"], 2)
        self.assertEqual(parameters["labels"]["type"], "array<string>")
        parameter_file = next(item for report in data["packages"] for item in report["parameter_files"] if item["file"].endswith("probe.yaml"))
        self.assertEqual({item["selector"] for item in parameter_file["selectors"]}, {"probe_node", "/**", "/robot/sensors/python_probe"})

    def test_colcon_artifacts_and_ignore_markers_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_minimal_package(root / "src" / "real_pkg", "real_pkg")
            self.write_minimal_package(root / "build" / "real_pkg", "real_pkg")
            self.write_minimal_package(root / "src" / "ignored_pkg", "ignored_pkg")
            (root / "src" / "ignored_pkg" / "COLCON_IGNORE").write_text("", encoding="utf-8")
            self.write_minimal_package(root / "src" / "ament_ignored", "ament_ignored")
            (root / "src" / "ament_ignored" / "AMENT_IGNORE").write_text("", encoding="utf-8")

            data = ros_repo_discover.scan_repository(root)

        self.assertEqual(data["package_count"], 1)
        self.assertEqual(data["packages"][0]["package"]["name"], "real_pkg")

    def test_diagnostics_detect_type_qos_dependency_and_launch_failures(self):
        combined = ros_repo_discover.scan_repository(FIXTURES)
        combined_codes = {item["code"] for item in combined["diagnostics"]}
        self.assertIn("RD201", combined_codes)
        self.assertIn("RD203", combined_codes)
        self.assertIn("RD204", combined_codes)
        self.assertIn("RD205", combined_codes)
        self.assertIn("RD206", combined_codes)
        self.assertIn("RD207", combined_codes)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "broken_pkg"
            self.write_minimal_package(package, "broken_pkg")
            (package / "src").mkdir()
            (package / "src" / "node.py").write_text("from geometry_msgs.msg import Twist\n", encoding="utf-8")
            (package / "launch").mkdir()
            (package / "launch" / "broken.launch.xml").write_text(
                '<launch><include file="missing.launch.py"/><node pkg="broken_pkg" exec="node"><param from="missing.yaml"/></node></launch>',
                encoding="utf-8",
            )
            data = ros_repo_discover.scan_repository(root)

        codes = {item["code"] for item in data["diagnostics"]}
        self.assertIn("RD101", codes)
        self.assertIn("RD301", codes)
        self.assertIn("RD302", codes)

    def test_every_inventory_entity_has_evidence_and_confidence(self):
        data = ros_repo_discover.scan_repository(FIXTURES)
        keys = (
            "executables",
            "node_names",
            "publishers",
            "subscriptions",
            "service_servers",
            "service_clients",
            "action_servers",
            "action_clients",
            "declared_parameters",
            "parameter_overrides",
            "plugins",
        )
        for report in data["packages"]:
            for key in keys:
                for item in report[key]:
                    self.assertIn("confidence", item)
                    self.assertTrue(item["evidence"])
                    self.assertIn(item["fact_type"], {"detected", "inferred", "diagnostic"})

    def test_stable_schema_and_empty_repository_behavior(self):
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            data = ros_repo_discover.scan_repository(Path(directory))
            output = Path(directory) / "reports"
            written = generate_project_overviews.write_documents(Path(directory), output)
            content = "\n".join(path.read_text(encoding="utf-8") for path in written)

        self.assertEqual(data["schema_version"], schema["properties"]["schema_version"]["const"])
        self.assertEqual(data["package_count"], 0)
        self.assertIn("RD001", {item["code"] for item in data["diagnostics"]})
        self.assertIn("No ROS 2 packages were detected", content)
        self.assertNotIn("TurtleBot", content)
        self.assertNotIn("Nav2", content)

    @unittest.skipUnless(TURTLEBOT4.exists(), "pinned TurtleBot 4 checkout is not available")
    def test_pinned_turtlebot_counts_and_report_precision(self):
        manifest = json.loads((Path(__file__).parent / "real_repositories.json").read_text(encoding="utf-8"))
        expected = manifest["repositories"][0]["expected"]
        data = ros_repo_discover.scan_repository(TURTLEBOT4)
        node_report = next(item for item in data["packages"] if item["package"]["name"] == "turtlebot4_node")

        for field in ("packages", "launch_files", "nodes", "topics", "services", "actions"):
            self.assertEqual(data["summary"][field], expected[field])
        self.assertEqual(
            {item["name"] for item in node_report["service_clients"]},
            {"e_stop", "robot_power", "start_motor", "stop_motor", "oakd/start_camera", "oakd/stop_camera"},
        )
        self.assertEqual(
            {item["name"] for item in node_report["action_clients"]},
            {"dock", "undock", "wall_follow", "led_animation"},
        )
        self.assertTrue(
            {f"hmi/led/{name}" for name in ("_power", "_motors", "_comms", "_wifi", "_battery", "_user1", "_user2")}
            <= {item["name"] for item in node_report["publishers"]}
        )
        self.assertFalse(any(item.get("type") == "urdf/gazebo sensor" for item in data["architecture"]["sensors"]))
        self.assertFalse(any(item.get("name") in {"${joint_name}", "${link_name}"} for item in data["architecture"]["sensors"]))
        self.assertFalse(any(item["name"] in {"nh_", "topic", "rclcpp::Node::SharedPtr nh"} for item in data["architecture"]["topics"]))
        self.assertFalse(any(item["code"] in {"RD201", "RD301", "RD302"} for item in data["diagnostics"]))

        with tempfile.TemporaryDirectory() as directory:
            report_path = generate_project_overviews.write_documents(TURTLEBOT4, Path(directory))[0]
            basic_report = report_path.read_text(encoding="utf-8")
        self.assertIn("| Services | 6 |", basic_report)
        self.assertIn("| Actions | 6 |", basic_report)
        self.assertIn("| Topics | 19 |", basic_report)
        self.assertNotIn("urdf/gazebo sensor", basic_report)
        self.assertNotIn("topic_nh", basic_report)

    def test_schema_constrains_entities_and_launch_records(self):
        schema = json.loads((WORKSPACE / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8"))
        definitions = schema["$defs"]

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(definitions["packageReport"]["properties"]["publishers"]["items"]["$ref"], "#/$defs/finding")
        self.assertEqual(definitions["launchGraph"]["properties"]["files"]["items"]["$ref"], "#/$defs/launchFile")
        self.assertEqual(definitions["launchFile"]["properties"]["actions"]["items"]["$ref"], "#/$defs/launchAction")
        self.assertEqual(definitions["launchFile"]["properties"]["includes"]["items"]["$ref"], "#/$defs/launchInclude")
        self.assertEqual(definitions["architecture"]["properties"]["nodes"]["items"]["$ref"], "#/$defs/node")
        self.assertEqual(definitions["architecture"]["properties"]["services"]["items"]["$ref"], "#/$defs/serviceActionGraph")
        self.assertEqual(definitions["architecture"]["properties"]["actions"]["items"]["$ref"], "#/$defs/serviceActionGraph")
        self.assertTrue({"kind", "name", "type", "file", "line", "confidence", "resolved", "evidence"} <= set(definitions["finding"]["required"]))
        self.assertTrue({"file", "format", "package", "actions", "includes", "arguments", "confidence", "evidence"} <= set(definitions["launchFile"]["required"]))
        self.assertTrue({"id", "name", "namespace", "package", "origin", "active", "publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients", "parameters", "confidence", "evidence"} <= set(definitions["node"]["required"]))

    def test_packaged_schema_matches_root_contract(self):
        root_schema = (WORKSPACE / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8")
        packaged_schema = resources.files("robot_doctor").joinpath("schemas/robot_doctor_scan.schema.json").read_text(encoding="utf-8")

        self.assertEqual(json.loads(packaged_schema), json.loads(root_schema))

    def test_scan_matches_json_schema_when_validator_is_installed(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("install the test extra for formal JSON Schema validation")
        schema = json.loads((WORKSPACE / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(ros_repo_discover.scan_repository(FIXTURES))

    @staticmethod
    def write_minimal_package(path: Path, name: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "package.xml").write_text(
            f'''<?xml version="1.0"?>
<package format="3">
  <name>{name}</name>
  <version>0.1.0</version>
  <description>fixture</description>
  <maintainer email="fixture@example.com">Fixture</maintainer>
  <license>MIT</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <export><build_type>ament_cmake</build_type></export>
</package>
''',
            encoding="utf-8",
        )
        (path / "CMakeLists.txt").write_text(
            f"cmake_minimum_required(VERSION 3.8)\nproject({name})\nfind_package(ament_cmake REQUIRED)\nament_package()\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
