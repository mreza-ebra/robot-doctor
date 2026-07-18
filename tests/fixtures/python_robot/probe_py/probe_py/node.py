import rclpy
from example_interfaces.action import Fibonacci
from rclpy.action import ActionServer
from rclpy.lifecycle import LifecycleNode
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import Trigger


class ProbeNode(Node):
    def __init__(self):
        super().__init__("probe_node")
        sensor_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.publisher = self.create_publisher(String, "status", sensor_qos)
        self.subscription = self.create_subscription(
            String,
            "status",
            self.on_status,
            QoSProfile(reliability=ReliabilityPolicy.RELIABLE),
        )
        self.service = self.create_service(Trigger, "reset", self.on_reset)
        self.client = self.create_client(Trigger, "remote_reset")
        self.action_server = ActionServer(self, Fibonacci, "compute", self.execute)
        self.declare_parameter("rate", 10)

    def on_status(self, message):
        return message

    def on_reset(self, request, response):
        return response

    def execute(self, goal_handle):
        return Fibonacci.Result()


class ManagedProbe(LifecycleNode):
    def __init__(self):
        super().__init__("managed_probe")


def main():
    rclpy.init()
    rclpy.spin(ProbeNode())
