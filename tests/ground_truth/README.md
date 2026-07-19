# Accuracy Ground Truth

These labels were manually transcribed from the fixture and TurtleBot source files. They are intentionally independent of Robot Doctor output and must not be regenerated from the scanner.

The benchmark compares resolved entities by package and name. Communication entities additionally compare the basename of the ROS type, so a C++ alias such as `Dock` can match `irobot_create_msgs/action/Dock` while a genuinely different interface cannot.

When source changes, review the source manually and update the labels and rationale in the same change.
