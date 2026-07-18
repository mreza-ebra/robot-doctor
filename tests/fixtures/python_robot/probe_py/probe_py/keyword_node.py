import rclpy
from control_msgs.action import FollowJointTrajectory
from example_interfaces.action import Fibonacci
from rclpy.action import ActionClient, ActionServer
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import String
from std_srvs.srv import Empty
from std_srvs.srv import Trigger


class KeywordNode(Node):
    def __init__(self):
        super().__init__(node_name="keyword_node")
        self.publisher = self.create_publisher(
            msg_type=String,
            topic="keyword_status",
            qos_profile=10,
        )
        self.subscription = self.create_subscription(
            msg_type=String,
            topic="keyword_command",
            callback=self.on_command,
            qos_profile=10,
        )
        self.mismatch_subscription = self.create_subscription(
            msg_type=Imu,
            topic="keyword_status",
            callback=self.on_command,
            qos_profile=10,
        )
        self.service = self.create_service(
            srv_type=Trigger,
            srv_name="keyword_reset",
            callback=self.on_reset,
        )
        self.client = self.create_client(
            srv_type=Trigger,
            srv_name="keyword_remote_reset",
        )
        self.action_server = ActionServer(
            node=self,
            action_type=Fibonacci,
            action_name="keyword_compute",
            execute_callback=self.execute,
        )
        self.action_client = ActionClient(
            node=self,
            action_type=Fibonacci,
            action_name="keyword_remote_compute",
        )
        self.mismatch_service_server = self.create_service(
            srv_type=Trigger,
            srv_name="shared_service",
            callback=self.on_reset,
        )
        self.mismatch_service_client = self.create_client(
            srv_type=Empty,
            srv_name="shared_service",
        )
        self.mismatch_action_server = ActionServer(
            node=self,
            action_type=Fibonacci,
            action_name="shared_action",
            execute_callback=self.execute,
        )
        self.mismatch_action_client = ActionClient(
            node=self,
            action_type=FollowJointTrajectory,
            action_name="shared_action",
        )
        self.declare_parameter(name="keyword_rate", value=25)

    def on_command(self, message):
        return message

    def on_reset(self, request, response):
        return response

    def execute(self, goal_handle):
        return Fibonacci.Result()


def main():
    rclpy.init()
    rclpy.spin(KeywordNode())
