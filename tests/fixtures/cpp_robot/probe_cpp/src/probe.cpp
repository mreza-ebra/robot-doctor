#include <example_interfaces/action/fibonacci.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <std_srvs/srv/trigger.hpp>

#include "probe_cpp/wrappers.hpp"

class ProbeCpp : public rclcpp::Node
{
public:
  ProbeCpp() : Node("probe_cpp_node")
  {
    publisher_ = create_publisher<sensor_msgs::msg::Imu>("status", rclcpp::QoS(10).best_effort());
    publisher_helper_.connect(shared_from_this(), "wrapped_status");
    subscription_ = create_subscription<sensor_msgs::msg::Imu>("imu", rclcpp::QoS(10), [](auto) {});
    service_ = create_service<std_srvs::srv::Trigger>("calibrate", [](auto, auto) {});
    client_ = create_client<std_srvs::srv::Trigger>("remote_calibrate");
    action_server_ = rclcpp_action::create_server<example_interfaces::action::Fibonacci>(
      this, "sequence", [](auto, auto) {}, [](auto) {}, [](auto) {});
    wrapped_service_ = std::make_unique<ServiceWrapper<std_srvs::srv::Trigger>>(
      shared_from_this(), "wrapped_reset");
    wrapped_action_ = std::make_unique<ActionWrapper<example_interfaces::action::Fibonacci>>(
      shared_from_this(), "wrapped_sequence");
    declare_parameter<double>("gain", 1.0);
  }

private:
  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr publisher_;
  PublisherHelper publisher_helper_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr subscription_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr client_;
  rclcpp_action::Server<example_interfaces::action::Fibonacci>::SharedPtr action_server_;
  std::unique_ptr<ServiceWrapper<std_srvs::srv::Trigger>> wrapped_service_;
  std::unique_ptr<ActionWrapper<example_interfaces::action::Fibonacci>> wrapped_action_;
};
