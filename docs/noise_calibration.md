# Diagnostic Noise Calibration

## Pinned Workspace Baseline

The pinned 95-package ROS 2 workspace contains ROS 2 Examples, ROS 2 Demos, MoveIt 2, and ros2_control.

| Policy | Errors | Warnings | Notes |
| --- | ---: | ---: | --- |
| Previous behavior / `dependency_mode=all` | 0 | 164 | All inferred dependency references become warnings. |
| Default / `dependency_mode=direct` | 0 | 38 | 126 indirect dependency guesses move to informational findings. |

The default warning set contains 32 direct dependency findings, three CMake install findings, one topic-type warning, one action-type warning, and one launch executable warning. Strict mode remains available for maintainers who want every inferred dependency elevated.

The architecture contains 348 active nodes out of 351 total source definitions and launch instances: 104 production, 73 test, and 171 example nodes. Test-only topic, service, action, and QoS graph entities produce no production-health findings. Interface mismatches become errors only when distinct, unconditional, resolved production nodes appear in the same launch file.

## Unresolved Entities

The same workspace contains 720 resolved and 169 unresolved static entities. Unresolved expressions remain in JSON because deleting them would hide analysis uncertainty. Users can:

- pass `--resolved-only` for concise text output;
- use the basic report instead of the expert unresolved-expression table;
- suppress selected diagnostic codes without deleting source facts;
- raise `minimum_diagnostic_confidence` for a quieter diagnostic stream.

## Feedback Controls

Repository-specific policy belongs in `.robot-doctor.json`. Use `ignore_dependencies` for global dependency patterns, `ignore_dependency_pairs` for `package:dependency` patterns, and `severity_overrides` or `suppress_diagnostics` for reviewed exceptions.

This calibration is pinned by real-repository regression tests. It should be reviewed whenever diagnostic semantics or the repository revisions change.
