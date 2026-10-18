# Real public issue: MoveIt Resources Panda RViz launch

This is an issue-guided reproduction against unmodified public source, followed by a reviewed local alternative. It is not a synthetic fault, blind discovery, upstream endorsement, or proof of runtime success.

## Report and pinned source

- [moveit/moveit_resources issue #219](https://github.com/moveit/moveit_resources/issues/219), open when checked on 18 October 2026.
- Reported symptom: `ros2 launch moveit_resources_panda_moveit_config moveit_rviz.launch.py` raises `PackageNotFoundError` for `moveit2_tutorials` on Lyrical.
- Current `ros2` branch snapshot at capture: `e1657c128ea61621777a041e3783c9c7c93db4ca`.
- [External package lookup, line 50](https://github.com/moveit/moveit_resources/blob/e1657c128ea61621777a041e3783c9c7c93db4ca/panda_moveit_config/launch/moveit_rviz.launch.py#L50).
- [Existing full RViz configuration](https://github.com/moveit/moveit_resources/blob/e1657c128ea61621777a041e3783c9c7c93db4ca/panda_moveit_config/launch/moveit.rviz) and [empty configuration](https://github.com/moveit/moveit_resources/blob/e1657c128ea61621777a041e3783c9c7c93db4ca/panda_moveit_config/launch/moveit_empty.rviz).
- [Existing CMake install rule](https://github.com/moveit/moveit_resources/blob/e1657c128ea61621777a041e3783c9c7c93db4ca/panda_moveit_config/CMakeLists.txt#L7) includes the launch directory and both configuration files.

## What the analyzer found

The existing Robot Doctor detector at `c89b4e3` flags RD101: `moveit_resources_panda_moveit_config` references `moveit2_tutorials` without declaring it. The evidence is the literal package-share lookup at line 50. No scanner change or repository-specific detection rule was needed for this second case.

The initial full scan contains two warnings. After the local correction it contains one: the target finding changes from one to none, and the unrelated `tf2_ros` dependency warning remains identical. This is not a declaration that the repository is healthy.

## The reviewed correction

The generic analyzer hint suggests declaring the dependency. Source review finds a better local alternative for this example: use the RViz assets already shipped in the Panda package.

[fix.patch](fix.patch) changes three lines in `panda_moveit_config/launch/moveit_rviz.launch.py`:

1. Resolve `moveit_resources_panda_moveit_config` instead of `moveit2_tutorials`.
2. Select `moveit.rviz` for the full mode.
3. Select `moveit_empty.rviz` for tutorial mode.

The manifest, dependency declarations, CMake install rules, and RViz files remain unchanged. The original checkout remains pristine; a separate corrected copy is scanned. No upstream comment or pull request has been submitted.

This example complements FANUC: one correction declares a required dependency, while this correction removes an external lookup. A dependency warning needs source review; its generic patch hint is not an automatic repair.

## Verification and limits

- The patch applies cleanly to the pinned pristine source.
- The corrected launch file parses as Python without executing it.
- Both local RViz files exist and the launch directory has an explicit CMake install rule.
- Both full and tutorial modes retain their distinct full/empty file selections.
- The target finding disappears; every unrelated diagnostic remains identical.
- The initial and corrected scans are generated from the same upstream revision.

No ROS build, launch, simulation, physical robot operation, or RViz visual comparison was performed. The package-local presets can differ from the tutorial presets previously referenced. These static checks establish the removed external lookup and available local assets; they do not prove every cause of the user's runtime failure is resolved. The reported issue remains open as of the recorded check.

## Evidence

- [Case narrative](index.html), [provenance/checks](case.json), and [reviewed patch](fix.patch).
- [Original detector scan](initial.json).
- [Before HTML](before.html) / [JSON](before.json).
- [After HTML](after.html) / [JSON](after.json).
- [Upstream attribution](THIRD_PARTY_NOTICES.md) and exact upstream license files in `licenses/`.

The local app provides both real examples at `/case-studies`; this case is at `/case-studies/moveit`. These are pinned saved snapshots, not live tracking of upstream changes.

## Repeat it

From the Robot Doctor checkout, use new clone/output directories:

```bash
git clone --branch ros2 https://github.com/moveit/moveit_resources.git /tmp/moveit-public-case
git -C /tmp/moveit-public-case checkout --detach e1657c128ea61621777a041e3783c9c7c93db4ca
python3 tools/run_moveit_case_study.py /tmp/moveit-public-case --output-dir /tmp/moveit-case-evidence --initial-report docs/case_studies/moveit/initial.json
```

Add `--corrected-output /path/to/new-fixed-copy` to keep the corrected source. The output copy must not already exist. The replay refuses a dirty checkout or revision drift. The script itself makes no network calls, upstream writes, or ROS executions. The initial snapshot preserves the pre-change detector result; replay validates that snapshot and reruns the current scanner. CI independently fetches and replays the pinned public source.
