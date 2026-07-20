# Accuracy Ground Truth

These labels were manually transcribed from source. They are intentionally independent of Robot Doctor output and must not be regenerated from scanner results.

The benchmark compares complete package/name/type identities. Selected cases also compare whether a finding is resolved, score launch files/actions/includes/arguments, and score diagnostic code plus affected graph subject. Large repositories use explicit package and source-file scopes so every finding in a measured scope is labeled; they do not claim that a partially labeled repository is complete.

| Benchmark | Manual source scope | Labels |
| --- | --- | ---: |
| Python fixture | `tests/fixtures/python_robot` entities | 24 |
| C++ fixture | `tests/fixtures/cpp_robot` entities | 11 |
| Launch fixture | Three files under `tests/fixtures/launch_robot/launch_probe/launch` | 15 |
| Diagnostic fixture | `RD201`–`RD207` findings produced by intentional Python fixture conflicts | 12 |
| TurtleBot 4 | Selected wrapper entities at revision `7fd29fb420e906f3aca4a904adb54b69b11c7c00` | 38 |
| MoveIt 2 | `moveit_servo/src/servo_node.cpp` at revision `4d841063574c31a21f69ae12a39fddb77a7eb984` | 8 |
| ros2_control | `controller_manager/src/controller_manager.cpp` at revision `2db79031a5464b0a3737d2c4de7f68c64ee93ef4` | 13 |

The total is 121 labels. Pinned benchmarks fail when the checkout revision differs, even if entity keys happen to match.

When source changes, review the source manually and update the labels and rationale in the same change.
