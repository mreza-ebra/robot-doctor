# Diagnostic Noise Calibration

## Pinned Workspace Baseline

The pinned 95-package ROS 2 workspace contains ROS 2 Examples, ROS 2 Demos, MoveIt 2, and ros2_control.

| Policy | Errors | Warnings | Notes |
| --- | ---: | ---: | --- |
| Strict / `dependency_mode=all` | 0 | 118 | All non-test inferred dependency references become warnings. |
| Default / `dependency_mode=direct` | 0 | 30 | 88 indirect dependency guesses remain informational findings. |

The default warning set contains 26 direct dependency findings, one CMake install finding, one topic-type warning, one action-type warning, and one launch executable warning. The full default report contains 184 findings: 30 warnings and 154 informational findings. Strict mode remains available for maintainers who want every non-test inferred dependency elevated.

The architecture contains 348 active nodes out of 351 total source definitions and launch instances: 104 production, 73 test, and 171 example nodes. All 351 node IDs are unique. Test-only topic, service, action, QoS, dependency, and executable-install entities produce no production-health findings. Interface mismatches become errors only when distinct, unconditional, resolved production nodes appear in the same launch file.

## Unresolved Entities

The same workspace contains 720 resolved and 169 unresolved static entities. Unresolved expressions remain in JSON because deleting them would hide analysis uncertainty. Users can:

- pass `--resolved-only` for concise text output;
- use the basic report instead of the expert unresolved-expression table;
- suppress selected diagnostic codes without deleting source facts;
- raise `minimum_diagnostic_confidence` for a quieter diagnostic stream.

## Feedback Controls

Repository-specific policy belongs in `.robot-doctor.json`. Use `ignore_dependencies` for global dependency patterns, `ignore_dependency_pairs` for `package:dependency` patterns, and `severity_overrides` or `suppress_diagnostics` for reviewed exceptions.

This calibration is pinned by real-repository regression tests. It should be reviewed whenever diagnostic semantics or the repository revisions change.
