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
- Generates basic, intermediate, and expert Markdown overviews with Mermaid diagrams.

## Installation

Python 3.10 or newer is sufficient. ROS and `colcon` are not required for static analysis; Python 3.10 uses `tomli` for modern packaging metadata.

```bash
python3 -m pip install -e .
```

This installs two commands:

```bash
robot-doctor-scan /path/to/repository
robot-doctor-overview /path/to/repository --output-dir project_overviews
```

The scripts also run directly without installation:

```bash
python3 tools/ros_repo_discover.py /path/to/repository
python3 tools/ros_repo_discover.py /path/to/repository --json --output scan.json
python3 tools/generate_project_overviews.py /path/to/repository --output-dir project_overviews
```

## Output Contract

JSON output uses schema version `1.1.0`; its machine-readable contract is in `schemas/robot_doctor_scan.schema.json` and is bundled in the installed `robot_doctor` package. The top-level sections are:

- `packages`: source-backed package and entity inventories.
- `launch_graph`: launch files, actions, includes, and include edges.
- `architecture`: source and launched nodes, resolved topic/service/action graphs, effective node parameters, TF/URDF data, inferred sensors, algorithms, actuation, and modification points.
- `diagnostics`: checks with stable codes, severity, evidence, and confidence.
- `limitations`: explicit boundaries of static analysis.

An unresolved name is retained as an expression with `resolved: false`; it is not silently converted to an empty authoritative-looking fact.

## Validation

Run the automated suite:

```bash
python3 -m pip install -e ".[test]"
python3 -m unittest discover -s tests -v
```

The persisted validation set under `tests/fixtures/` contains unrelated Python, C++, and launch-focused ROS 2 packages, including keyword APIs, modern entry points, nested parameter YAML, and generic templated C++ wrappers. `tests/real_repositories.json` pins TurtleBot 4, ROS 2 Examples, ROS 2 Demos, MoveIt 2, and ros2_control, plus a combined 95-package workspace. CI verifies exact package, launch, node, topic, service, and action inventories with zero high-confidence error diagnostics.

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

Robot Doctor does not claim runtime certainty. Dynamic names, substitutions, external packages, plugin loading, runtime TF, and actual DDS QoS negotiation require a built or running system for confirmation. Type mismatches are errors only when endpoints are proven to share a node or launch deployment; otherwise they remain lower-confidence warnings.
