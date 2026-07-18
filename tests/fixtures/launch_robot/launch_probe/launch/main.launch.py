from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import XMLLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import ComposableNodeContainer, Node, PushRosNamespace
from launch_ros.descriptions import ComposableNode


def generate_launch_description():
    share = get_package_share_directory("launch_probe")
    child = PathJoinSubstitution([share, "launch", "child.launch.xml"])
    parameters = PathJoinSubstitution([share, "config", "probe.yaml"])
    return LaunchDescription([
        DeclareLaunchArgument("enabled", default_value="true"),
        PushRosNamespace("robot"),
        Node(
            package="probe_py",
            executable="probe_node",
            name="python_probe",
            namespace="sensors",
            condition=IfCondition(LaunchConfiguration("enabled")),
            remappings=[("status", "robot_status")],
            parameters=[parameters],
        ),
        ComposableNodeContainer(
            name="components",
            namespace="robot",
            package="rclcpp_components",
            executable="component_container",
            composable_node_descriptions=[
                ComposableNode(package="probe_cpp", plugin="probe_cpp::Probe", name="cpp_component")
            ],
        ),
        IncludeLaunchDescription(
            XMLLaunchDescriptionSource(child),
            launch_arguments={"enabled": "true"}.items(),
        ),
    ])
