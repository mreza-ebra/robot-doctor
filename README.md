# Robot Doctor Static ROS 2 Analyzer

Robot Doctor statically inventories and diagnoses ROS 2 source repositories without building them or requiring a ROS installation. It is repository-agnostic: TurtleBot 4 is one validation target, not a source of hardcoded architecture.

## Capabilities

- Discovers packages while excluding `build/`, `install/`, `log/`, version-control data, and trees marked by `COLCON_IGNORE` or `AMENT_IGNORE`.
- Extracts positional and keyword-based Python ROS APIs with the Python AST, plus C++ entities and generic wrapper instantiations with balanced-call parsing.
- Finds nodes, publishers, subscribers, services, actions, parameters, QoS, lifecycle nodes, executables, custom interface fields, URDF transforms, sensors, and plugin declarations.
- Inventories Python executables from `setup.py`, `setup.cfg`, and `pyproject.toml` entry points.
- Builds launch graphs from Python, XML, and YAML launch files, including local includes, arguments, conditions, namespaces, remappings, parameter sources, and composed nodes.
- Builds node-level communication graphs with launch namespaces/remappings and typed parameter override precedence.
- Separates detected facts, inferred architecture, and diagnostics. Every finding includes evidence and a confidence score.
- Checks likely missing dependencies, topic/service/action type mismatches, orphan endpoints, QoS incompatibilities, broken launch references, missing parameter files, CMake install gaps, and invalid TF parentage/cycles.
- Skips unreadable, oversized, or excess source files with explicit diagnostics instead of aborting the scan.
- Supports progress callbacks, cancellation, diagnostic suppression, severity overrides, and dependency-noise controls.
- Accepts local folders or public HTTPS Git URLs from the CLI and provides a local browser interface for Git URLs and ZIP uploads.
- Generates basic, intermediate, and expert Markdown overviews with Mermaid diagrams.

## Installation

Python 3.10 or newer is sufficient. ROS and `colcon` are not required for static analysis; Python 3.10 uses `tomli` for modern packaging metadata.

```bash
python3 -m pip install -e .
```

This installs three commands:

```bash
robot-doctor-scan /path/to/repository
robot-doctor-overview /path/to/repository --output-dir project_overviews
robot-doctor-web
```

## Self-Service Web Flow

Start the local interface and press the scan button:

```bash
robot-doctor-web
```

The page accepts either a public `https://` Git repository URL or a ZIP upload. Scans run as background tasks with progress, cancellation, JSON download, and basic/intermediate/expert Markdown reports. The beta server refuses non-loopback hosts, validates Host and Origin headers, requires a CSRF token, and allows two active tasks by default. It still has no user authentication and is intentionally local-only, not a public hosting service. Results use temporary storage and disappear when the application closes; use CLI `--output` or download the files to retain them.

The CLI also accepts Git directly:

```bash
robot-doctor-scan https://github.com/ros2/examples.git --json --output examples.json --progress
```

The scripts also run directly without installation:

```bash
python3 tools/ros_repo_discover.py /path/to/repository
python3 tools/ros_repo_discover.py /path/to/repository --json --output scan.json
python3 tools/generate_project_overviews.py /path/to/repository --output-dir project_overviews
python3 tools/robot_doctor_web.py
```

## Output Contract

JSON output uses schema version `1.2.0`; its machine-readable contract is in `schemas/robot_doctor_scan.schema.json` and is bundled in the installed `robot_doctor` package. The top-level sections are:

- `packages`: source-backed package and entity inventories.
- `configuration`: effective limits, diagnostic policy, and suppressed-diagnostic count.
- `launch_graph`: launch files, actions, includes, and include edges.
- `architecture`: source and launched nodes, resolved topic/service/action graphs, effective node parameters, TF/URDF data, inferred sensors, algorithms, actuation, and modification points.
- `diagnostics`: checks with stable codes, severity, evidence, and confidence.
- `limitations`: explicit boundaries of static analysis.

An unresolved name is retained as an expression with `resolved: false`; it is not silently converted to an empty authoritative-looking fact.

## Configuration And Noise Controls

Copy `.robot-doctor.example.json` to a local `<repository>/.robot-doctor.json` for automatic loading, or pass `--config FILE`. Automatically discovered configuration is trusted only for local-folder scans; Git URL and ZIP inputs cannot raise limits or suppress diagnostics through their own files. The configuration schema is `schemas/robot_doctor_config.schema.json`.

```bash
robot-doctor-scan my_robot --dependency-mode direct --suppress RD202 --severity RD101=info --resolved-only
```

- `dependency_mode=direct` keeps direct CMake/launch omissions as warnings and downgrades indirect include/import guesses to information.
- `dependency_mode=all` restores strict warning behavior; `off` disables dependency suggestions.
- `suppress_diagnostics`, `severity_overrides`, dependency ignore patterns, and minimum confidence provide repository-specific feedback controls.
- `max_file_size_bytes`, `max_total_size_bytes`, `max_files`, and `max_repository_entries` bound reads and streaming traversal. Skips or truncation are reported as `RD004`, `RD005`, `RD007`, `RD009`, or `RD010`.
- HTTPS Git intake has a one-GiB checkout cap by default. Override it explicitly with `--max-checkout-size-mb`; the local web administrator can also set `--max-concurrent-tasks`.

The measured warning reduction and unresolved-entity policy are documented in `docs/noise_calibration.md`.

## Validation

Run the automated suite:

```bash
python3 -m pip install -e ".[test]"
python3 -m unittest discover -s tests -v
```

The persisted validation set under `tests/fixtures/` contains unrelated Python, C++, and launch-focused ROS 2 packages, including keyword APIs, modern entry points, nested parameter YAML, and generic templated C++ wrappers. `tests/real_repositories.json` pins TurtleBot 4, ROS 2 Examples, ROS 2 Demos, MoveIt 2, and ros2_control, plus a combined 95-package workspace. CI verifies exact package, launch, node, topic, service, and action inventories with zero high-confidence error diagnostics.

Aggregate regressions are complemented by manually curated entity labels in `tests/ground_truth/`. The benchmark measures precision and recall rather than regenerating expected counts from scanner output:

```bash
python3 tests/run_accuracy_benchmark.py --require-all --output accuracy.json
```

The current set contains 121 manually reviewed labels across Python/C++ fixtures, TurtleBot 4, scoped MoveIt Servo and ros2_control production files, launch graph records, and diagnostic cases. It requires 1.000 precision and recall within each explicit scope. Interface keys retain complete package/kind/type identity, and pinned real-source labels fail on revision drift. Scope and provenance are documented in `tests/ground_truth/README.md`.

Run the real-repository gate after placing the pinned checkouts at their manifest paths:

```bash
python3 tests/run_real_repository_regressions.py --require-all
```

Build and verify a distributable wheel:

```bash
python3 -m pip install build
python3 -m build
python3 tests/check_wheel.py dist/*.whl
```

TurtleBot 4 can also be scanned manually:

```bash
python3 tools/ros_repo_discover.py turtlebot4 --json --output turtlebot4_discovery.json
```

## License

The current root license is proprietary and all rights are reserved. This conservative default avoids granting redistribution rights before the product's commercial/open-source strategy is decided; replace it explicitly if an open-source release is intended.

## Static-Analysis Limits

Robot Doctor does not claim runtime certainty. Dynamic names, substitutions, external packages, plugin loading, runtime TF, and actual DDS QoS negotiation require a built or running system for confirmation. Type mismatches are errors only when endpoints are proven to share a node or launch deployment; otherwise they remain lower-confidence warnings. An optional live ROS graph comparison remains a later phase because it requires a sourced ROS installation and a running robot or simulation.

Git URL intake currently rejects literal local/private addresses but does not pin resolved DNS addresses or independently validate every redirect destination. This is acceptable only inside the documented loopback beta boundary; complete `SECURITY.md` hosted-service controls before deployment beyond one trusted local user.
