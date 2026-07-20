# Changelog

All notable changes to Robot Doctor are documented here. Versions follow Semantic Versioning.

## [0.5.0] - 2026-07-20

### Added

- Repository-agnostic ROS 2 package, node, endpoint, parameter, launch, URDF, QoS, lifecycle, plugin, and interface discovery.
- Node-level topic, service, action, TF, parameter-precedence, and modification-point architecture views.
- Evidence-backed diagnostics for dependencies, graph mismatches, orphan endpoints, QoS, launch references, build metadata, and TF structure.
- Local web intake for public HTTPS Git URLs and ZIP uploads with progress, cancellation, downloadable JSON, and three report levels.
- Stable JSON and configuration schemas bundled in the wheel.
- Manually curated 121-label accuracy benchmark spanning fixtures, TurtleBot 4, MoveIt 2, ros2_control, launch graphs, and diagnostics.

### Security

- Bounded source reads, repository traversal, ZIP extraction, Git checkout size, and concurrent web tasks.
- Loopback-only web binding with Host, Origin, CSRF, and browser security-header enforcement.
- Remote repositories cannot automatically load their own `.robot-doctor.json` policy.

### Known Limits

- Static analysis cannot confirm dynamic runtime graph behavior, actual DDS QoS negotiation, runtime TF, or plugin loading.
- Web results are temporary and disappear when the local application exits.
- Git intake is local-beta only; hosted deployment additionally requires DNS/redirect destination hardening and service-level authentication and quotas.
