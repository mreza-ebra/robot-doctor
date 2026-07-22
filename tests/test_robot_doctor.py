from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib import resources
from pathlib import Path
from unittest import mock

WORKSPACE = Path(__file__).parents[1]
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor import overviews as generate_project_overviews
from robot_doctor import scanner as ros_repo_discover
from robot_doctor.config import ScanConfig


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
        self.assertEqual(
            ros_repo_discover.resolve_cpp_type("rclcpp::TypeAdapter<std::string, std_msgs::msg::String>", {}),
            "std_msgs/msg/String",
        )
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

    def test_ros2_control_model_covers_hardware_interfaces_plugins_and_guidance(self):
        data = ros_repo_discover.scan_repository(FIXTURES / "cpp_robot")
        control = data["architecture"]["ros2_control"]

        component = next(item for item in control["hardware_components"] if item["name"] == "ProbeSystem")
        self.assertEqual(component["plugin"], "probe_cpp/ProbeSystemHardware")
        self.assertEqual(component["command_interfaces"], ["drive_joint/velocity"])
        self.assertEqual(component["state_interfaces"], ["drive_joint/position", "drive_joint/velocity"])
        command = next(item for item in control["command_interfaces"] if item["identifier"] == "drive_joint/velocity")
        self.assertEqual(command["parameters"], {"min": "-2.0", "max": "2.0"})
        self.assertIn("ProbeImu", {item["name"] for item in control["hardware_components"]})
        self.assertIn("control_imu/orientation.x", {item["identifier"] for item in control["state_interfaces"]})
        generated = next(item for item in control["hardware_components"] if item["name"] == "GeneratedLiftSystem")
        self.assertEqual(generated["source"], "xacro")
        self.assertEqual(generated["command_interfaces"], ["lift_joint/position"])
        self.assertTrue(generated["resolved"])
        self.assertTrue(any(record["extractor"] == "xacro_macro_invocation" for record in generated["evidence"]))
        self.assertNotIn("WrongSystem", {item["name"] for item in control["hardware_components"]})
        self.assertNotIn("UnincludedSystem", {item["name"] for item in control["hardware_components"]})
        self.assertFalse(any("${" in item["name"] for item in control["hardware_components"]))
        configured_controller = next(item for item in control["controllers"] if item["name"] == "drive_controller")
        self.assertEqual(configured_controller["joints"], ["drive_joint"])
        self.assertEqual(configured_controller["command_interfaces"], ["velocity"])
        self.assertEqual(configured_controller["state_interfaces"], ["position", "velocity"])
        self.assertIn("drive_transmission", {item["name"] for item in control["transmissions"]})
        self.assertIn("lift_joint_transmission", {item["name"] for item in control["transmissions"]})
        self.assertIn("probe_cpp/ProbeTransmission", {item["name"] for item in control["transmissions"]})

        plugins = {item["name"]: item for item in control["plugins"]}
        self.assertEqual(plugins["probe_cpp/ProbeSystemHardware"]["base_class_type"], "hardware_interface::SystemInterface")
        self.assertEqual(plugins["probe_cpp/ProbeController"]["role"], "ros2_control controller")
        algorithm_names = {item["name"] for item in data["architecture"]["algorithms"]}
        actuation_names = {item["name"] for item in data["architecture"]["actuation"]}
        sensor_names = {item["name"] for item in data["architecture"]["sensors"]}
        self.assertIn("probe_cpp/ProbeController", algorithm_names)
        self.assertNotIn("probe_cpp/ProbeSystemHardware", algorithm_names)
        self.assertIn("probe_cpp/ProbeSystemHardware", actuation_names)
        self.assertIn("drive_joint/velocity", actuation_names)
        self.assertIn("probe_cpp/ProbeSensorHardware", sensor_names)
        self.assertIn("ProbeImu", sensor_names)
        self.assertNotIn("control_imu", sensor_names)
        modification_tasks = {item["task"] for item in data["architecture"]["modification_points"]}
        self.assertIn("Change ros2_control hardware components and plugin wiring", modification_tasks)
        self.assertIn("Configure or implement ros2_control controllers", modification_tasks)
        self.assertIn("Change ros2_control transmission mappings or loaders", modification_tasks)
        self.assertEqual(data["summary"]["services"], len(data["architecture"]["services"]))
        chain = next(item for item in control["control_chains"] if item["controller"] == "drive_controller")
        self.assertEqual(
            (chain["command_interface"], chain["hardware_component"], chain["resource"], chain["transmission"], chain["actuators"]),
            ("drive_joint/velocity", "ProbeSystem", "drive_joint", "drive_transmission", ["drive_motor"]),
        )
        self.assertTrue(chain["resolved"])
        self.assertEqual(chain["match_status"], "unique_match")
        generated_chain = next(item for item in control["control_chains"] if item["command_interface"] == "lift_joint/position")
        self.assertIsNone(generated_chain["controller"])
        self.assertFalse(generated_chain["resolved"])
        self.assertEqual(generated_chain["match_status"], "unclaimed")

    def test_control_chain_does_not_cross_link_same_named_robots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller_package = root / "controller_config"
            self.write_minimal_package(controller_package, "controller_config")
            (controller_package / "controllers.yaml").write_text(
                """controller_manager:
  ros__parameters:
    drive_controller:
      type: controller_config/DriveController
drive_controller:
  ros__parameters:
    joints: [shared_joint]
    command_interfaces: [velocity]
""",
                encoding="utf-8",
            )
            for package_name in ("robot_a", "robot_b"):
                package = root / package_name
                self.write_minimal_package(package, package_name)
                urdf = package / "urdf" / "robot.urdf"
                urdf.parent.mkdir()
                urdf.write_text(
                    f"""<robot name="{package_name}">
  <ros2_control name="{package_name}_system" type="system">
    <hardware><plugin>{package_name}/System</plugin></hardware>
    <joint name="shared_joint"><command_interface name="velocity"/></joint>
  </ros2_control>
</robot>
""",
                    encoding="utf-8",
                )
            data = ros_repo_discover.scan_repository(root)
            shutil.rmtree(root / "robot_b")
            single_candidate_data = ros_repo_discover.scan_repository(root)

        chains = [
            item
            for item in data["architecture"]["ros2_control"]["control_chains"]
            if item.get("controller") == "drive_controller"
        ]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["match_status"], "ambiguous")
        self.assertFalse(chains[0]["resolved"])
        self.assertIsNone(chains[0]["hardware_component"])
        self.assertEqual(len(chains[0]["candidate_hardware_components"]), 2)
        self.assertTrue(any(value.startswith("robot_a:") for value in chains[0]["candidate_hardware_components"]))
        self.assertTrue(any(value.startswith("robot_b:") for value in chains[0]["candidate_hardware_components"]))
        single_candidate = next(
            item
            for item in single_candidate_data["architecture"]["ros2_control"]["control_chains"]
            if item.get("controller") == "drive_controller"
        )
        self.assertEqual(single_candidate["match_status"], "cross_package_candidate")
        self.assertFalse(single_candidate["resolved"])
        self.assertIsNone(single_candidate["hardware_component"])
        self.assertEqual(len(single_candidate["candidate_hardware_components"]), 1)

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

    def test_unreadable_and_oversized_files_are_skipped_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "probe"
            self.write_minimal_package(package, "probe")
            source = package / "src" / "large.cpp"
            source.parent.mkdir()
            source.write_text("x" * 2_000, encoding="utf-8")
            data = ros_repo_discover.scan_repository(root, config=ScanConfig(max_file_size_bytes=1_000))
            total_limited = ros_repo_discover.scan_repository(
                root,
                config=ScanConfig(max_file_size_bytes=10_000, max_total_size_bytes=1_000),
            )

        self.assertIn("RD005", {item["code"] for item in data["diagnostics"]})
        self.assertEqual(data["summary"]["skipped_files"], 1)
        self.assertIn("RD009", {item["code"] for item in total_limited["diagnostics"]})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "unreadable"
            self.write_minimal_package(package, "unreadable")
            original_read_text = Path.read_text

            def guarded_read_text(path: Path, *args, **kwargs):
                if path.name == "package.xml":
                    raise PermissionError("fixture permission denied")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", new=guarded_read_text):
                data = ros_repo_discover.scan_repository(root)

        self.assertEqual(data["package_count"], 0)
        self.assertIn("RD004", {item["code"] for item in data["diagnostics"]})

    def test_repository_enumeration_and_candidate_limits_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(8):
                package = root / f"package_{index}"
                self.write_minimal_package(package, f"package_{index}")
            file_limited = ros_repo_discover.scan_repository(
                root,
                config=ScanConfig(max_files=2, max_repository_entries=100),
            )
            entry_limited = ros_repo_discover.scan_repository(
                root,
                config=ScanConfig(max_files=100, max_repository_entries=3),
            )

        self.assertLessEqual(file_limited["package_count"], 2)
        self.assertIn("RD007", {item["code"] for item in file_limited["diagnostics"]})
        self.assertIn("RD010", {item["code"] for item in entry_limited["diagnostics"]})
        self.assertEqual(entry_limited["configuration"]["max_repository_entries"], 3)

    def test_remote_input_does_not_auto_load_repository_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".robot-doctor.json").write_text(
                '{"max_files": 999999, "suppress_diagnostics": ["RD001"]}',
                encoding="utf-8",
            )
            local_config = ros_repo_discover.load_scan_config_for_input(None, root, "local")
            remote_config = ros_repo_discover.load_scan_config_for_input(None, root, "git")
            explicit_remote_config = ros_repo_discover.load_scan_config_for_input(root / ".robot-doctor.json", root, "git")

        self.assertEqual(local_config.max_files, 999999)
        self.assertEqual(remote_config, ScanConfig())
        self.assertEqual(explicit_remote_config.max_files, 999999)

    def test_progress_cancellation_and_diagnostic_policy(self):
        events = []
        data = ros_repo_discover.scan_repository(FIXTURES / "python_robot", progress=events.append)
        self.assertEqual(events[-1]["stage"], "complete")
        self.assertEqual(events[-1]["current"], events[-1]["total"])
        self.assertGreater(data["summary"]["resolved_entities"], 0)

        checks = iter((False, False, True))
        with self.assertRaises(ros_repo_discover.ScanCancelled):
            ros_repo_discover.scan_repository(FIXTURES, cancel_check=lambda: next(checks, True))

        configured = ros_repo_discover.scan_repository(
            FIXTURES,
            config=ScanConfig(suppress_diagnostics=frozenset({"RD201"}), severity_overrides={"RD206": "info"}),
        )
        self.assertNotIn("RD201", {item["code"] for item in configured["diagnostics"]})
        self.assertTrue(all(item["severity"] == "info" for item in configured["diagnostics"] if item["code"] == "RD206"))
        self.assertGreater(configured["configuration"]["suppressed_diagnostics"], 0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".robot-doctor.json").write_text('{"suppress_diagnostics": ["RD001"]}', encoding="utf-8")
            configured = ros_repo_discover.scan_repository(root)
        self.assertNotIn("RD001", {item["code"] for item in configured["diagnostics"]})
        self.assertEqual(configured["configuration"]["suppressed_diagnostics"], 1)

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
        self.assertTrue(all(item["severity"] == "info" for item in data["diagnostics"] if item["code"] == "RD101"))
        self.assertTrue(all(item["remediation"]["steps"] for item in data["diagnostics"]))
        dependency_finding = next(item for item in data["diagnostics"] if item["code"] == "RD101")
        self.assertIn("<depend>geometry_msgs</depend>", dependency_finding["remediation"]["patch_hint"])
        self.assertIn("rosdep check --from-paths src --ignore-src", dependency_finding["remediation"]["commands"])
        for item in combined["diagnostics"]:
            commands = "\n".join(item["remediation"]["commands"])
            if item.get("topic"):
                self.assertNotIn("<topic>", commands)
                self.assertIn(item["topic"], commands)
            if item.get("interface") and item["code"] in {"RD204", "RD205", "RD206", "RD207"}:
                self.assertIn(item["interface"], commands)
        mismatch_commands = {
            code: "\n".join(next(item for item in combined["diagnostics"] if item["code"] == code)["remediation"]["commands"])
            for code in ("RD201", "RD204", "RD206")
        }
        self.assertIn("ros2 interface show std_msgs/msg/String", mismatch_commands["RD201"])
        self.assertIn("ros2 interface show std_srvs/srv/Trigger", mismatch_commands["RD204"])
        self.assertIn("ros2 interface show example_interfaces/action/Fibonacci", mismatch_commands["RD206"])
        self.assertNotIn("<interface-type>", "\n".join(mismatch_commands.values()))

    def test_test_entities_do_not_pollute_production_diagnostics_or_node_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "scope_probe"
            self.write_minimal_package(package, "scope_probe")
            (package / "src").mkdir()
            (package / "src" / "production.cpp").write_text(
                r'''
#include <geometry_msgs/msg/polygon.hpp>
#include <geometry_msgs/msg/polygon_stamped.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <rclcpp/rclcpp.hpp>

class ProductionNode : public rclcpp::Node
{
public:
  ProductionNode() : Node("production_node")
  {
    publisher_ = create_publisher<geometry_msgs::msg::Twist>("cmd_vel", 10);
    if (use_stamped_) {
      stamped_ = create_subscription<geometry_msgs::msg::PolygonStamped>("footprint", 10, [](auto) {});
    } else {
      plain_ = create_subscription<geometry_msgs::msg::Polygon>("footprint", 10, [](auto) {});
    }
  }
};
''',
                encoding="utf-8",
            )
            (package / "test").mkdir()
            (package / "test" / "test_cmd_vel.cpp").write_text(
                r'''
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <std_msgs/msg/string.hpp>

void string_case(const rclcpp::NodeOptions & options)
{
  auto node = std::make_shared<rclcpp::Node>(options);
  node->create_publisher<std_msgs::msg::String>("cmd_vel", 10);
}

void imu_case()
{
  auto node = rclcpp::Node(dwa_gen);
  auto alternate = rclcpp::Node(accel);
  node.create_publisher<sensor_msgs::msg::Imu>("cmd_vel", 10);
}
''',
                encoding="utf-8",
            )
            (package / "setup.py").write_text(
                "from setuptools import setup\nsetup(name='scope_probe', packages=['scope_probe'], install_requires=['setuptools'])\n",
                encoding="utf-8",
            )

            data = ros_repo_discover.scan_repository(root)

        report = data["packages"][0]
        self.assertEqual({item["name"] for item in report["node_names"]}, {"production_node"})
        self.assertEqual(report["executables"], [])
        self.assertFalse(any(item["code"] == "RD201" and item.get("topic") == "cmd_vel" for item in data["diagnostics"]))
        footprint = next(item for item in data["diagnostics"] if item["code"] == "RD201" and item.get("topic") == "footprint")
        self.assertEqual(footprint["severity"], "warning")
        self.assertIn("not proven to run together", footprint["message"])
        active_nodes = [item for item in data["architecture"]["nodes"] if item["active"]]
        self.assertEqual(data["summary"]["nodes"], len(active_nodes))
        self.assertEqual(data["summary"]["architecture_nodes_total"], len(data["architecture"]["nodes"]))
        self.assertEqual(sum(data["summary"]["node_scopes"].values()), data["summary"]["nodes"])
        self.assertGreater(data["summary"]["node_scopes"]["production"], 0)
        self.assertGreater(data["summary"]["node_scopes"]["test"], 0)
        cmd_vel = next(item for item in data["architecture"]["topics"] if item["name"] == "cmd_vel")
        self.assertEqual(set(cmd_vel["deployment_scopes"]), {"production", "test"})
        for key in ("sensors", "actuation", "algorithms"):
            self.assertTrue(all(item.get("deployment_scope") in {"production", "test", "example"} for item in data["architecture"][key]))
        self.assertTrue(any(item["deployment_scope"] == "test" for item in data["architecture"]["sensors"]))
        self.assertTrue(any(item["deployment_scope"] == "test" for item in data["architecture"]["actuation"]))
        self.assertTrue(all(item["deployment_scope"] in {"production", "test", "example"} for item in data["architecture"]["modification_points"]))
        self.assertTrue(any("mutually exclusive branches" in item for item in data["limitations"]))

    def test_source_node_ids_are_unique_per_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "duplicate_nodes"
            self.write_minimal_package(package, "duplicate_nodes")
            (package / "src").mkdir()
            (package / "src" / "duplicate.cpp").write_text(
                '#include <rclcpp/rclcpp.hpp>\n'
                'auto first = rclcpp::Node("dwa_gen");\n'
                'auto second = rclcpp::Node("dwa_gen");\n',
                encoding="utf-8",
            )

            data = ros_repo_discover.scan_repository(root)

        node_ids = [item["id"] for item in data["architecture"]["nodes"]]
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertEqual([item["name"] for item in data["architecture"]["nodes"]].count("dwa_gen"), 2)

    def test_test_only_cmake_dependencies_and_targets_do_not_warn(self):
        self.assertTrue(ros_repo_discover.cmake_testing_context("if(BUILD_TESTING)\nadd_executable(test test.cpp)\n", 60))
        self.assertFalse(ros_repo_discover.cmake_testing_context("if(NOT BUILD_TESTING)\nadd_executable(prod prod.cpp)\n", 64))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "scoped_build"
            self.write_minimal_package(package, "scoped_build")
            (package / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.8)\nproject(scoped_build)\nfind_package(ament_cmake REQUIRED)\n"
                "if(BUILD_TESTING)\n"
                "  find_package(test_msgs REQUIRED)\n"
                "  add_executable(dwa_gen test/dwa_gen.cpp)\n"
                "  add_executable(benchmark_solver src/benchmark_solver.cpp)\n"
                "endif()\n"
                "find_package(geometry_msgs REQUIRED)\n"
                "add_executable(prod_node src/prod_node.cpp)\n"
                "ament_package()\n",
                encoding="utf-8",
            )
            system_package = root / "nav2_system_tests"
            self.write_minimal_package(system_package, "nav2_system_tests")
            (system_package / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.8)\nproject(nav2_system_tests)\n"
                "find_package(ament_cmake REQUIRED)\nfind_package(rclcpp REQUIRED)\n"
                "add_executable(dummy_controller_node src/dummy_controller_node.cpp)\n"
                "add_executable(dummy_planner_node src/dummy_planner_node.cpp)\nament_package()\n",
                encoding="utf-8",
            )

            data = ros_repo_discover.scan_repository(root)

        dependency_findings = [item for item in data["diagnostics"] if item["code"] == "RD101"]
        install_findings = [item for item in data["diagnostics"] if item["code"] == "RD104"]
        self.assertEqual({item["dependency"] for item in dependency_findings}, {"geometry_msgs"})
        self.assertEqual({item["deployment_scope"] for item in dependency_findings}, {"production"})
        self.assertEqual({item["remediation"]["patch_hint"] for item in install_findings}, {"install(TARGETS <target> DESTINATION lib/${PROJECT_NAME})"})
        self.assertEqual({item["message"].split("'")[1] for item in install_findings}, {"prod_node"})
        scoped_report = next(item for item in data["packages"] if item["package"]["name"] == "scoped_build")
        system_report = next(item for item in data["packages"] if item["package"]["name"] == "nav2_system_tests")
        executable_scopes = {item["name"]: item["deployment_scope"] for item in scoped_report["executables"]}
        self.assertEqual(executable_scopes, {"dwa_gen": "test", "benchmark_solver": "test", "prod_node": "production"})
        self.assertEqual({item["deployment_scope"] for item in system_report["executables"]}, {"test"})
        self.assertFalse(any(name in item["message"] for item in install_findings for name in ("dummy_controller_node", "dummy_planner_node")))

    def test_resolved_production_launch_proves_cross_node_type_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "deployed_probe"
            self.write_minimal_package(package, "deployed_probe")
            (package / "src").mkdir()
            (package / "src" / "publisher.cpp").write_text(
                '#include <rclcpp/rclcpp.hpp>\n#include <std_msgs/msg/string.hpp>\n'
                'class PublisherNode : public rclcpp::Node { public: PublisherNode() : Node("publisher_node") '
                '{ create_publisher<std_msgs::msg::String>("shared", 10); } };\n',
                encoding="utf-8",
            )
            (package / "src" / "subscriber.cpp").write_text(
                '#include <rclcpp/rclcpp.hpp>\n#include <sensor_msgs/msg/imu.hpp>\n'
                'class SubscriberNode : public rclcpp::Node { public: SubscriberNode() : Node("subscriber_node") '
                '{ create_subscription<sensor_msgs::msg::Imu>("shared", 10, [](auto) {}); } };\n',
                encoding="utf-8",
            )
            (package / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.8)\nproject(deployed_probe)\nfind_package(ament_cmake REQUIRED)\n"
                "add_executable(publisher src/publisher.cpp)\nadd_executable(subscriber src/subscriber.cpp)\n"
                "install(TARGETS publisher subscriber DESTINATION lib/${PROJECT_NAME})\nament_package()\n",
                encoding="utf-8",
            )
            (package / "launch").mkdir()
            launch_file = package / "launch" / "deployed.launch.xml"
            launch_file.write_text(
                '<launch><node pkg="deployed_probe" exec="publisher"/><node pkg="deployed_probe" exec="subscriber"/></launch>',
                encoding="utf-8",
            )

            data = ros_repo_discover.scan_repository(root)
            launch_file.write_text(
                '<launch><node pkg="deployed_probe" exec="publisher" if="$(var enabled)"/>'
                '<node pkg="deployed_probe" exec="subscriber" unless="$(var enabled)"/></launch>',
                encoding="utf-8",
            )
            conditional_data = ros_repo_discover.scan_repository(root)

        conflict = next(item for item in data["diagnostics"] if item["code"] == "RD201" and item.get("topic") == "shared")
        self.assertEqual(conflict["severity"], "error")
        self.assertEqual(conflict["deployment_scope"], "production")
        conditional_conflict = next(item for item in conditional_data["diagnostics"] if item["code"] == "RD201" and item.get("topic") == "shared")
        self.assertEqual(conditional_conflict["severity"], "warning")

    def test_scan_records_reproducibility_provenance(self):
        data = ros_repo_discover.scan_repository(FIXTURES / "python_robot")
        provenance = data["provenance"]

        self.assertTrue(provenance["started_at"].endswith("Z"))
        self.assertTrue(provenance["completed_at"].endswith("Z"))
        self.assertGreaterEqual(provenance["duration_seconds"], 0)
        self.assertEqual(set(provenance["git"]), {"commit_sha", "branch", "dirty"})
        self.assertEqual(set(provenance["input"]), {"source_type", "archive_sha256", "content_sha256"})
        self.assertTrue(provenance["environment"]["python_version"])
        self.assertIn("platform", provenance["environment"])

    @unittest.skipUnless(shutil.which("git"), "Git is required for the provenance security regression")
    def test_git_provenance_disables_repository_fsmonitor_and_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            package = root / "safe_package"
            self.write_minimal_package(package, "safe_package")
            subprocess.run(["git", "-C", str(root), "init", "--quiet"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Robot Doctor Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "robot-doctor@example.com"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "fixture"], check=True)
            marker = Path(directory) / "fsmonitor-executed"
            hook = Path(directory) / "fsmonitor-hook"
            hook.write_text(f"#!/bin/sh\nprintf invoked > '{marker}'\nprintf 'token\\n'\n", encoding="utf-8")
            hook.chmod(0o755)
            subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)

            data = ros_repo_discover.scan_repository(root)

            self.assertFalse(marker.exists(), "repository-local core.fsmonitor executed during provenance collection")
            self.assertIsNotNone(data["provenance"]["git"]["commit_sha"])

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
        self.assertIn("| Actions | 4 |", basic_report)
        self.assertIn("| Topics | 19 |", basic_report)
        self.assertNotIn("urdf/gazebo sensor", basic_report)
        self.assertNotIn("topic_nh", basic_report)

    def test_schema_constrains_entities_and_launch_records(self):
        schema = json.loads((WORKSPACE / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8"))
        definitions = schema["$defs"]

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["configuration"]["$ref"], "#/$defs/configuration")
        self.assertEqual(schema["properties"]["provenance"]["$ref"], "#/$defs/provenance")
        self.assertEqual(set(definitions["provenance"]["properties"]["input"]["required"]), {"source_type", "archive_sha256", "content_sha256"})
        self.assertEqual(definitions["packageReport"]["properties"]["publishers"]["items"]["$ref"], "#/$defs/finding")
        self.assertEqual(definitions["launchGraph"]["properties"]["files"]["items"]["$ref"], "#/$defs/launchFile")
        self.assertEqual(definitions["launchFile"]["properties"]["actions"]["items"]["$ref"], "#/$defs/launchAction")
        self.assertEqual(definitions["launchFile"]["properties"]["includes"]["items"]["$ref"], "#/$defs/launchInclude")
        self.assertEqual(definitions["architecture"]["properties"]["nodes"]["items"]["$ref"], "#/$defs/node")
        self.assertEqual(definitions["architecture"]["properties"]["services"]["items"]["$ref"], "#/$defs/serviceActionGraph")
        self.assertEqual(definitions["architecture"]["properties"]["actions"]["items"]["$ref"], "#/$defs/serviceActionGraph")
        self.assertEqual(definitions["architecture"]["properties"]["ros2_control"]["$ref"], "#/$defs/ros2Control")
        self.assertEqual(definitions["ros2Control"]["properties"]["hardware_components"]["items"]["$ref"], "#/$defs/controlEntity")
        self.assertEqual(definitions["ros2Control"]["properties"]["command_interfaces"]["items"]["$ref"], "#/$defs/controlInterface")
        self.assertEqual(definitions["ros2Control"]["properties"]["control_chains"]["items"]["$ref"], "#/$defs/controlChain")
        self.assertTrue({"match_status", "match_basis", "candidate_hardware_components"} <= set(definitions["controlChain"]["required"]))
        self.assertIn("cross_package_candidate", definitions["controlChain"]["properties"]["match_status"]["enum"])
        self.assertTrue({"kind", "name", "type", "file", "line", "confidence", "resolved", "evidence"} <= set(definitions["finding"]["required"]))
        self.assertTrue({"file", "format", "package", "actions", "includes", "arguments", "confidence", "evidence"} <= set(definitions["launchFile"]["required"]))
        self.assertTrue({"id", "name", "namespace", "package", "origin", "active", "publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients", "parameters", "confidence", "evidence"} <= set(definitions["node"]["required"]))
        self.assertIn("deployment_scope", definitions["node"]["required"])
        self.assertIn("launch_condition", definitions["node"]["required"])
        self.assertIn("deployment_scopes", definitions["topic"]["required"])
        self.assertTrue({"architecture_nodes_total", "node_scopes"} <= set(definitions["summary"]["required"]))
        self.assertIn("deployment_scope", definitions["modificationPoint"]["required"])
        self.assertIn("remediation", definitions["diagnostic"]["required"])
        self.assertEqual(definitions["diagnostic"]["properties"]["remediation"]["properties"]["patch_hint"]["$ref"], "#/$defs/nullableString")

    def test_packaged_schema_matches_root_contract(self):
        root_schema = (WORKSPACE / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8")
        packaged_schema = resources.files("robot_doctor").joinpath("schemas/robot_doctor_scan.schema.json").read_text(encoding="utf-8")
        root_config_schema = (WORKSPACE / "schemas" / "robot_doctor_config.schema.json").read_text(encoding="utf-8")
        packaged_config_schema = resources.files("robot_doctor").joinpath("schemas/robot_doctor_config.schema.json").read_text(encoding="utf-8")

        self.assertEqual(json.loads(packaged_schema), json.loads(root_schema))
        self.assertEqual(json.loads(packaged_config_schema), json.loads(root_config_schema))

    def test_scan_matches_json_schema(self):
        try:
            import jsonschema
        except ImportError as exc:
            self.fail(f"formal schema validation requires the declared test dependencies: {exc}")
        schema = json.loads((WORKSPACE / "schemas" / "robot_doctor_scan.schema.json").read_text(encoding="utf-8"))
        config_schema = json.loads((WORKSPACE / "schemas" / "robot_doctor_config.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator.check_schema(config_schema)
        jsonschema.Draft202012Validator(schema).validate(ros_repo_discover.scan_repository(FIXTURES))
        jsonschema.Draft202012Validator(config_schema).validate(json.loads((WORKSPACE / ".robot-doctor.example.json").read_text(encoding="utf-8")))

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
