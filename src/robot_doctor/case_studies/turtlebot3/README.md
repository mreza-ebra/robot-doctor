# Real public issue: TurtleBot3 standalone namespace argument

An issue-guided reproduction against unmodified public source, followed by a local reproduction of the already proposed upstream approach. This is not blind discovery, an original fix, upstream acceptance, or evidence of runtime success.

## Repository, adoption, and report

- Repository: [ROBOTIS-GIT/turtlebot3](https://github.com/ROBOTIS-GIT/turtlebot3), ROBOTIS's mobile-robot platform.
- Public adoption signals captured on 18 October 2026: **2,078 stars and 1,188 forks**, from the GitHub repository API. These measure public interest; clone counts are unavailable and no clone-count claim is made.
- [Issue #1149](https://github.com/ROBOTIS-GIT/turtlebot3/issues/1149) was open at capture. It reports that direct launch of the state publisher fails because `namespace` does not exist in the launch context.
- The reporter already describes the cause and supplies [proposed fix #1150](https://github.com/ROBOTIS-GIT/turtlebot3/pull/1150), also open at capture. This case reproduces that known approach; our work has not posted a comment or PR upstream.
- Pinned current `main` SHA: `fc817ce3073af1d6032397c64504134882af5e9a`.
- [Missing fallback at line 33](https://github.com/ROBOTIS-GIT/turtlebot3/blob/fc817ce3073af1d6032397c64504134882af5e9a/turtlebot3_bringup/launch/turtlebot3_state_publisher.launch.py#L33).

The report reproduces the failure with `TURTLEBOT3_MODEL=burger` and `ros2 launch turtlebot3_bringup turtlebot3_state_publisher.launch.py`. A parent launch can already supply the value; this case concerns standalone use or callers that omit it.

## What happened

| Stage | Target result | Full scan |
| --- | --- | --- |
| Original detector at `48cb3c1` | Missed the requirement | 1 error, 4 warnings, 5 informational findings |
| Improved detector, same source | One RD304 for `namespace` | 1 error, 4 warnings, 6 informational findings |
| Separate corrected copy | Target RD304 absent | 1 error, 4 warnings, 5 informational findings |

All unrelated diagnostics are identical before/after the correction. Their presence, including an unrelated diagnostic classified as an error, means this case does not declare the repository healthy. Only the target condition has been investigated here.

## Generic detector and correction

RD304 recognizes imported Python `LaunchConfiguration` calls with literal names that have neither a substitution default nor a local `DeclareLaunchArgument` or `SetLaunchConfiguration` for that name. Aliases and qualified imports are supported. Dynamic declarations, dynamic names, shadowed APIs, and uncertain keyword expansion are handled conservatively. No repository code is executed and no TurtleBot3 names are added to detection rules.

The diagnostic is **informational**: a child launch can intentionally require parent-supplied context. A declaration may also intentionally make an argument mandatory. The check does not prove reachability, action ordering, or failure in every deployment. This case's existing issue and source review establish the standalone-use requirement.

[fix.patch](fix.patch) changes one launch file:

- Add `default=''` to `LaunchConfiguration('namespace')`.
- Add `DeclareLaunchArgument('namespace', default_value='', description='Robot namespace')` to the returned launch description.

The same named substitution remains bound to the `namespace` variable, preserving the interface for explicit caller overrides. ROS's [LaunchConfiguration implementation](https://github.com/ros2/launch/blob/rolling/launch/launch/substitutions/launch_configuration.py) uses an existing context value before the fallback. That supports the intended behavior; it is not a runtime test of this correction.

## Verification and limits

- The patch applies cleanly to the pinned pristine source.
- The corrected launch file parses as Python.
- AST checks confirm one namespace lookup, one declaration, both empty defaults, and the unchanged named binding.
- The target RD304 changes from one finding to none; every unrelated diagnostic stays identical.
- The pristine checkout remains unchanged. A separate corrected copy is scanned, and the minimal patch is retained.

No ROS build/launch, simulation, or robot test was performed. `TURTLEBOT3_MODEL` remains required. Omitted namespace and an explicit value such as `namespace:=tb1` still need independent ROS testing, including the parent-launch path. The local correction does not close the upstream issue or establish that every symptom is resolved.

## Evidence and replay

- [Case narrative](index.html), [provenance and checks](case.json), [patch](fix.patch).
- [Original detector scan](initial.json).
- [Improved before HTML](before.html) / [JSON](before.json).
- [Corrected after HTML](after.html) / [JSON](after.json).
- [Attribution](THIRD_PARTY_NOTICES.md) and the exact upstream [Apache-2.0 license](licenses/Apache-2.0.txt).

The app exposes this pinned saved case at `/case-studies/turtlebot3`, alongside FANUC and MoveIt at `/case-studies`. The snapshots do not automatically track later upstream source, issue status, or adoption counts.

Use new directories from the Robot Doctor checkout:

```bash
git clone --branch main https://github.com/ROBOTIS-GIT/turtlebot3.git /tmp/turtlebot3-public-case
git -C /tmp/turtlebot3-public-case checkout --detach fc817ce3073af1d6032397c64504134882af5e9a
python3 tools/run_turtlebot3_case_study.py /tmp/turtlebot3-public-case --output-dir /tmp/turtlebot3-case-evidence --initial-report docs/case_studies/turtlebot3/initial.json
```

Add `--corrected-output /path/to/new-fixed-copy` to retain the corrected source. The script refuses a dirty checkout, revision drift, and evidence/corrected paths inside the pristine checkout. It does not fetch code, execute ROS, or write upstream. The initial report preserves the historical detector miss; replay validates that snapshot and reruns the current detector. CI separately fetches the pinned source and repeats the comparison.
