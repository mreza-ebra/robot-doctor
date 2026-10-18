"""Small, offline ROS 2 project with one deliberately introduced launch fault.

These are real source files scanned by the normal analyzer, not canned findings.
No ROS code is executed when preparing or scanning the example.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

LAUNCH_PATH = "scan_demo/launch/scan.launch.py"
BROKEN_REFERENCE = '"missing.yaml"'
FIXED_REFERENCE = '"scan.yaml"'

FILES = {
    "scan_demo/package.xml": '''<package format="3">
  <name>scan_demo</name><version>0.1.0</version>
  <description>Robot Doctor controlled scan example</description>
  <maintainer email="demo@example.com">Demo maintainer</maintainer>
  <license>LicenseRef-Proprietary</license>
  <buildtool_depend>ament_python</buildtool_depend>
  <depend>rclpy</depend><depend>sensor_msgs</depend>
  <exec_depend>launch</exec_depend><exec_depend>launch_ros</exec_depend>
  <export><build_type>ament_python</build_type></export>
</package>
''',
    "scan_demo/setup.py": '''from setuptools import setup

setup(name="scan_demo", version="0.1.0", packages=["scan_demo"],
      data_files=[("share/ament_index/resource_index/packages", ["resource/scan_demo"]),
                  ("share/scan_demo", ["package.xml"]),
                  ("share/scan_demo/launch", ["launch/scan.launch.py"]),
                  ("share/scan_demo/config", ["config/scan.yaml"])],
      entry_points={"console_scripts": ["scan_source = scan_demo.nodes:source_main",
                                         "scan_observer = scan_demo.nodes:observer_main"]})
''',
    "scan_demo/setup.cfg": "[develop]\nscript_dir=$base/lib/scan_demo\n[install]\ninstall_scripts=$base/lib/scan_demo\n",
    "scan_demo/resource/scan_demo": "",
    "scan_demo/scan_demo/__init__.py": "",
    "scan_demo/scan_demo/nodes.py": '''import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


class ScanSource(Node):
    def __init__(self):
        super().__init__("scan_source")
        self.declare_parameter("frame_id", "laser")
        self.publisher = self.create_publisher(LaserScan, "/scan", qos_profile_sensor_data)
        self.timer = self.create_timer(0.5, self.publish_scan)

    def publish_scan(self):
        message = LaserScan()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = self.get_parameter("frame_id").value
        message.angle_min, message.angle_max, message.angle_increment = -0.5, 0.5, 0.5
        message.range_min, message.range_max = 0.1, 10.0
        message.ranges = [1.0, 1.5, 2.0]
        self.publisher.publish(message)


class ScanObserver(Node):
    def __init__(self):
        super().__init__("scan_observer")
        self.subscription = self.create_subscription(
            LaserScan, "/scan", self.receive_scan, qos_profile_sensor_data)

    def receive_scan(self, message):
        self.get_logger().info(f"Received {len(message.ranges)} synthetic ranges")


def run(node_class):
    rclpy.init()
    node = node_class()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def source_main():
    run(ScanSource)


def observer_main():
    run(ScanObserver)
''',
    "scan_demo/config/scan.yaml": "scan_source:\n  ros__parameters:\n    frame_id: laser\n",
    LAUNCH_PATH: '''from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    return LaunchDescription([
        Node(package="scan_demo", executable="scan_source", name="scan_source",
             parameters=[PathJoinSubstitution([FindPackageShare("scan_demo"), "config", "missing.yaml"])]),
        Node(package="scan_demo", executable="scan_observer", name="scan_observer"),
    ])
''',
    "README.md": '''# Controlled ROS 2 scan example

This synthetic project publishes and observes /scan. It is not a real sensor,
navigation stack, or customer incident. The example uses the product license.

The broken version intentionally refers to config/missing.yaml in
scan_demo/launch/scan.launch.py. The supplied file is config/scan.yaml.
The corrected version changes only that reference. Run robot-doctor-scan .
to reproduce the static result. No ROS installation is needed for that scan.

Static verification does not establish build success or runtime behavior.
To test ROS execution separately in a sourced ROS 2 environment:
colcon build --symlink-install
source install/setup.bash
ros2 launch scan_demo scan.launch.py
ros2 topic echo /scan --once
No ROS execution or hardware compatibility is claimed by this example.
''',
    "LICENSE": "Robot Doctor Proprietary License\n\nCopyright (c) 2026 Robot Doctor contributors. All rights reserved.\n\nThis software and its associated documentation are proprietary. No permission is\ngranted to use, copy, modify, distribute, sublicense, or sell any portion of the\nsoftware without prior written authorization from the copyright holder.\n\nThird-party projects and dependencies retain their respective licenses.\n",
}


def example_files(stage: str) -> dict[str, str]:
    if stage not in {"broken", "fixed"}:
        raise ValueError("unknown example stage")
    files = dict(FILES)
    if stage == "fixed":
        files[LAUNCH_PATH] = files[LAUNCH_PATH].replace(BROKEN_REFERENCE, FIXED_REFERENCE)
    return files


def write_example(root: Path, stage: str) -> None:
    for name, content in example_files(stage).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def example_zip(stage: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in example_files(stage).items():
            archive.writestr(name, content)
    return buffer.getvalue()
