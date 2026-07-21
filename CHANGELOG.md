# Changelog

All notable changes to Robot Doctor are documented here. Versions follow Semantic Versioning.

## [0.5.0] - 2026-07-20

### Added

- Repository-agnostic ROS 2 package, node, endpoint, parameter, launch, URDF, QoS, lifecycle, plugin, and interface discovery.
- Node-level topic, service, action, TF, parameter-precedence, and modification-point architecture views.
- Evidence-backed diagnostics for dependencies, graph mismatches, orphan endpoints, QoS, launch references, build metadata, and TF structure.
- Local web intake for public or token-authenticated HTTPS Git URLs and ZIP uploads with progress, cancellation, rendered HTML, downloadable JSON, and three report levels.
- Stable JSON and configuration schemas bundled in the wheel.
- Manually curated 121-label accuracy benchmark spanning fixtures, TurtleBot 4, MoveIt 2, ros2_control, launch graphs, and diagnostics.
- Rendered HTML results with prioritized findings, architecture diagrams and tables, remediation guidance, and reproducibility metadata.
- Private HTTPS Git cloning through ephemeral, read-only access-token configuration.
- Docker Compose and macOS double-click launchers that avoid a local Python/pip setup.
- Node-to-interface topology, severity/package filters, collapsed informational findings, and ZIP archive/content hashes in rendered results.

### Security

- Bounded source reads, repository traversal, ZIP extraction, Git checkout size, and concurrent web tasks.
- Loopback-only web binding with Host, Origin, CSRF, and browser security-header enforcement.
- Remote repositories cannot automatically load their own `.robot-doctor.json` policy.
- Private Git tokens are excluded from clone arguments, task records, and scan artifacts.
- ZIP intake rejects `.git` metadata before extraction, and provenance commands disable hooks, repository fsmonitor executables, global/system configuration, and untracked-cache refreshes.
- Local browser submissions accept an opaque `Origin: null` only with a validated loopback Host and CSRF token.
- The Docker image and Compose service run as fixed unprivileged UID/GID `10001:10001`, with CI rejecting root execution.
- HTTPS Git intake rejects non-public DNS answers, pins Git/libcurl to validated addresses, disables redirects and proxies, and disallows file/external-helper transports.

### Known Limits

- Static analysis cannot confirm dynamic runtime graph behavior, actual DDS QoS negotiation, runtime TF, or plugin loading.
- Web results are temporary and disappear when the local application exits.
- Git intake is local-beta only; hosted deployment additionally requires DNS/redirect destination hardening and service-level authentication and quotas.
