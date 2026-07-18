#pragma once

#include <memory>
#include <string>

#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <sensor_msgs/msg/imu.hpp>

struct PublisherHelper
{
  void connect(std::shared_ptr<rclcpp::Node> node, std::string topic)
  {
    publisher_ = node->create_publisher<sensor_msgs::msg::Imu>(topic, 10);
  }

  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr publisher_;
};

template<typename ServiceT>
class BaseServiceWrapper
{
public:
  BaseServiceWrapper(std::shared_ptr<rclcpp::Node> node, std::string service)
  {
    client_ = node->create_client<ServiceT>(service);
  }

private:
  typename rclcpp::Client<ServiceT>::SharedPtr client_;
};

template<typename ServiceT>
class ServiceWrapper : public BaseServiceWrapper<ServiceT>
{
public:
  ServiceWrapper(std::shared_ptr<rclcpp::Node> node, std::string service)
  : BaseServiceWrapper<ServiceT>(node, service)
  {}
};

template<typename ActionT>
class ActionWrapper
{
public:
  ActionWrapper(std::shared_ptr<rclcpp::Node> node, std::string action)
  {
    client_ = rclcpp_action::create_client<ActionT>(node, action);
  }

private:
  typename rclcpp_action::Client<ActionT>::SharedPtr client_;
};
