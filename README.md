# Robot Doctor Static ROS 2 Analyzer

Robot Doctor statically inventories and diagnoses ROS 2 source repositories without building them or requiring a ROS installation. It is repository-agnostic: TurtleBot 4 is one validation target, not a source of hardcoded architecture.

## Capabilities

- Discovers packages while excluding `build/`, `install/`, `log/`, version-control data, and trees marked by `COLCON_IGNORE` or `AMENT_IGNORE`.
- Extracts positional and keyword-based Python ROS APIs with the Python AST, plus C++ entities and generic wrapper instantiations with balanced-call parsing.
- Finds nodes, publishers, subscribers, services, actions, parameters, QoS, lifecycle nodes, executables, custom interface fields, URDF transforms, sensors, and plugin declarations.
- Inventories Python executables from `setup.py`, `setup.cfg`, and `pyproject.toml` entry points.
- Builds launch graphs from Python, XML, and YAML launch files, including local includes, arguments, conditions, namespaces, remappings, parameter sources, and composed nodes.
- Builds node-level communication graphs with launch namespaces/remappings and typed parameter override precedence.
- Separates detected facts, inferred architecture, and diagnostics. Every finding includes evidence, confidence, concrete repair steps, verification commands, suggested files, and an optional patch hint.
- Checks likely missing dependencies, topic/service/action type mismatches, orphan endpoints, QoS incompatibilities, broken launch references, missing parameter files, CMake install gaps, and invalid TF parentage/cycles.
- Skips unreadable, oversized, or excess source files with explicit diagnostics instead of aborting the scan.
- Supports progress callbacks, cancellation, diagnostic suppression, severity overrides, and dependency-noise controls.
- Accepts local folders and public or token-authenticated HTTPS Git URLs from the CLI, with DNS-pinned, redirect-disabled Git transport and hardened ZIP uploads in the local browser interface.
- Generates basic, intermediate, and expert Markdown overviews with Mermaid diagrams.
- Records scan timestamps, duration, Git revision/branch/dirty state, input type, archive/content SHA-256, ROS distribution, Python version, and platform metadata.

## One-Command Docker Start (Docker Required)

The external-pilot path requires Docker Desktop. It removes the Python, pip, ROS, `colcon`, and Terminal setup, but it is not zero-install. A truly zero-install macOS experience still requires a separately distributed, signed, and notarized application bundle.

- On macOS, double-click `start_robot_doctor.command`, then use the browser page it opens.
- On any Docker Compose system, run `docker compose up --build` and open `http://127.0.0.1:8765`.
- Double-click `stop_robot_doctor.command` on macOS, or run `docker compose down`, to stop it.

The container publishes only to host loopback, runs as fixed unprivileged UID/GID `10001:10001` with no Linux capabilities, uses a read-only filesystem plus temporary scan storage, and keeps the same local-only security boundary as the Python launcher.

## Python Installation

Python 3.10.15 or newer is required so security-sensitive IP address classification includes the Python 3.10 maintenance fixes. ROS and `colcon` are not required for static analysis; Python 3.10 uses `tomli` for modern packaging metadata.

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

The page accepts a public or private `https://` Git repository URL, with an optional read-only access token, or a ZIP upload. Token and diagnostic-noise controls are grouped under **Advanced options**. Before cloning, Robot Doctor resolves the Git hostname in an isolated resolver process with a ten-second deadline, terminates that process on scan cancellation or timeout, rejects the entire lookup if any address is non-public, pins Git/libcurl to the accepted addresses, disables proxies and HTTP redirects, and ignores global/system Git configuration. Tokens are supplied to the clone process through ephemeral environment configuration and are not stored in tasks or reports; keep them repository-scoped, read-only, and short-lived. Scans run as background tasks with progress and cancellation. Completed scans render prioritized diagnostics, repair guidance, severity/package filters, a node-to-interface topology, architecture tables, and provenance directly in HTML, with JSON and basic/intermediate/expert Markdown downloads. Informational findings are collapsed by default. The beta server validates loopback Host and Origin headers, accepts the opaque `Origin: null` value used by the local in-app browser only when the Host remains loopback, requires a CSRF token, and allows two active tasks by default. It still has no user authentication and is intentionally local-only, not a public hosting service. Results use temporary storage and disappear when the application closes; use CLI `--output` or download the files to retain them.

The CLI also accepts Git directly:

```bash
robot-doctor-scan https://github.com/ros2/examples.git --json --output examples.json --progress
```

For a private GitHub repository, create a fine-grained read-only token and pass only its environment-variable name:

```bash
export ROBOT_DOCTOR_GIT_TOKEN='your-read-only-token'
robot-doctor-scan https://github.com/owner/private-repository.git --git-token-env ROBOT_DOCTOR_GIT_TOKEN --json --output scan.json
```

The token is never accepted inside the URL or as a literal CLI argument, reducing shell-history and process-list exposure.

The scripts also run directly without installation:

```bash
python3 tools/ros_repo_discover.py /path/to/repository
python3 tools/ros_repo_discover.py /path/to/repository --json --output scan.json
python3 tools/generate_project_overviews.py /path/to/repository --output-dir project_overviews
python3 tools/robot_doctor_web.py
```

## Output Contract

JSON output uses schema version `1.4.0`; its machine-readable contract is in `schemas/robot_doctor_scan.schema.json` and is bundled in the installed `robot_doctor` package. Architecture nodes and interfaces include `production`, `test`, or `example` deployment scope so test fixtures do not contaminate production diagnostics. The top-level sections are:

- `packages`: source-backed package and entity inventories.
- `configuration`: effective limits, diagnostic policy, and suppressed-diagnostic count.
- `launch_graph`: launch files, actions, includes, and include edges.
- `architecture`: source and launched nodes, resolved topic/service/action graphs, effective node parameters, TF/URDF data, inferred sensors, algorithms, actuation, and modification points.
- `diagnostics`: checks with stable codes, severity, evidence, confidence, repair steps, verification commands, suggested files, and patch hints.
- `provenance`: timestamps, duration, Git state, input type, ZIP archive/content SHA-256 when applicable, ROS distribution, Python version, and operating-system metadata needed to reproduce a scan.
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
- HTTPS Git intake requires Git 2.37 or newer for pinned hostname resolution, gives DNS ten seconds by default, runs each lookup in an isolated process that is terminated on cancellation or timeout, disables redirects, and has a one-GiB checkout cap. Override the checkout cap explicitly with `--max-checkout-size-mb`; the local web administrator can also set `--max-concurrent-tasks`.

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

CI additionally runs the real network intake path without DNS or subprocess mocks:

```bash
python3 tests/run_live_git_intake.py
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

ZIP intake rejects every `.git` path component, including case and trailing-space variants, before extraction. Git provenance collection ignores global/system configuration and disables repository hooks, `core.fsmonitor`, and the untracked cache. Git URL intake rejects literal or DNS-resolved non-public addresses, pins each clone to the validated address set, and rejects redirects instead of trusting a new destination. Private-repository tokens remain appropriate only for the documented local workflow and should be repository-scoped, read-only, and short-lived. Hosted or multi-user deployment remains unsupported until every control in `SECURITY.md` is implemented.
