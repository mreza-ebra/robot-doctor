# Real public issue: FANUC driver missing runtime dependency

This is an issue-guided reproduction against unmodified public source, followed by a reviewed local correction. It is not a synthetic fault or a claim of blind discovery, customer traction, or upstream acceptance.

## Report and source

- Upstream issue: [FANUC-CORPORATION/fanuc_driver #50](https://github.com/FANUC-CORPORATION/fanuc_driver/issues/50), open when checked on 18 October 2026.
- Pinned current `main` revision at capture: `da5db3f611cdc9dffe687e056c4e79bea584bf5c`.
- Reported symptom: a selective build of `fanuc_moveit_config` was followed by a launch failure because `fanuc_hardware_interface` was unavailable.
- [Frozen launch reference, line 41](https://github.com/FANUC-CORPORATION/fanuc_driver/blob/da5db3f611cdc9dffe687e056c4e79bea584bf5c/fanuc_moveit_config/launch/fanuc_moveit.launch.py#L41).
- [Frozen package manifest](https://github.com/FANUC-CORPORATION/fanuc_driver/blob/da5db3f611cdc9dffe687e056c4e79bea584bf5c/fanuc_moveit_config/package.xml).

The upstream reporter had already identified the missing dependency and mentioned ros_glint. This work tests and improves FaciRob against that known issue; it does not claim exclusive discovery or competitive superiority.

## What actually happened

| Stage | Result |
| --- | --- |
| Original Robot Doctor at `4bb7afb` | Missed the target dependency. Full scan: 7 warnings and 80 informational findings. |
| Improved detector, same unmodified upstream SHA | RD101 identifies the missing `fanuc_hardware_interface` declaration in `fanuc_moveit_config`. Eight literal lookup locations provide evidence. Full scan: 11 warnings and 80 informational findings. |
| Separate corrected copy | Add one `exec_depend` entry in `fanuc_moveit_config/package.xml`. The target RD101 disappears. Full scan: 10 warnings and 80 informational findings. |

The additional four warnings after improving the detector reflect newly covered package-share references, not new upstream defects introduced by this work. Only the target finding was investigated and corrected. All other diagnostics are identical between the improved-detector before/after scans.

The generic analyzer change recognizes import-qualified literal `FindPackageShare`, `get_package_share_directory`, and `get_package_share_path` calls, including aliases. It does not execute launch code, guess dynamic package names, or add FANUC names to scanner rules. Tests cover unrelated same-named functions, shadowed/reassigned imports, test-only references, declared dependencies, and disabled dependency checks.

## The correction and verification

[fix.patch](fix.patch) adds the runtime dependency required by the launch code. The pristine checkout is unchanged. A separate corrected copy was produced locally; nothing was submitted to the upstream issue or repository.

Checks performed:

- The unified patch applies cleanly to the pinned pristine checkout with `git apply --check`.
- The corrected package manifest parses as XML.
- The new local package dependency edge introduces no local cycle.
- Manifest-derived local dependency closure changes from `fanuc_moveit_config` alone to also include `fanuc_hardware_interface` and `fanuc_libs`. External packages are omitted from this local-only calculation; this is not a colcon execution.
- The target RD101 changes from one finding to none; every unrelated diagnostic remains identical.
- The unchanged source and corrected copy were scanned without building or executing them.

No ROS build, launch, simulation, or physical robot test was performed. The verified result is the corrected missing manifest declaration and its static consequences. Other runtime causes, missing dependencies, or upstream issues may remain. The upstream issue is still open as of the recorded check; a local correction does not close it.

## Inspect the evidence

- [Case narrative](index.html)
- [Case provenance and checks](case.json)
- [Original detector scan](initial.json)
- [Improved detector: before](before.html) / [JSON](before.json)
- [Improved detector: after](after.html) / [JSON](after.json)
- [Upstream attribution](THIRD_PARTY_NOTICES.md) and [Apache-2.0 license](licenses/Apache-2.0.txt)

The local app exposes the same saved case at `/case-study`; it clearly labels the pinned snapshot and verification limits. The offline showcase includes a `public_case/` folder with the same evidence. These captured pages do not automatically track later upstream changes.

## Repeat it

From the Robot Doctor checkout:

```bash
git clone https://github.com/FANUC-CORPORATION/fanuc_driver.git /tmp/fanuc-public-case
git -C /tmp/fanuc-public-case checkout --detach da5db3f611cdc9dffe687e056c4e79bea584bf5c
python3 tools/run_public_case_study.py /tmp/fanuc-public-case --output-dir /tmp/fanuc-case-evidence --initial-report docs/case_studies/fanuc/initial.json
```

Use a new directory for the clone. The replay refuses revision drift or a dirty checkout, scans the pristine source, creates a separate temporary copy, applies the manifest correction there, checks the diagnostic delta, and exports evidence. Add `--corrected-output /path/to/new-fixed-copy` to retain the corrected copy. That directory must not already exist. No network activity, ROS execution, upstream write, or robot operation is performed by the replay script itself.

The checked-in initial report documents the old detector's miss; replaying the script with the improved detector does not rerun the historical implementation. CI separately fetches the pinned upstream source and replays the corrected detector and local patch.
