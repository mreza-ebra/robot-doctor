# Security Policy

## Supported Versions

Robot Doctor is currently a local-only beta. Security fixes target the latest `0.5.x` release candidate; older snapshots are unsupported.

## Reporting A Vulnerability

Do not open a public issue containing exploit details. Use the official GitHub repository's enabled private vulnerability-reporting feature so disclosure remains private and structured.

Include the affected version or commit, operating system, reproduction steps, impact, and whether untrusted repository content is required. Expect an acknowledgement within five business days; remediation timing depends on severity and reproducibility.

## Deployment Boundary

`robot-doctor-web` is designed for one user on a loopback interface. Direct launches reject non-loopback binding. The supplied container runs as fixed unprivileged UID/GID `10001:10001` and explicitly binds inside Docker to `0.0.0.0`, while Compose publishes the port only to host `127.0.0.1`; Host validation remains loopback-only. The application applies CSRF, Origin, Host, task-count, upload, extraction, checkout, traversal, and read limits. These controls do not make it a multi-user hosted service.

Do not expose the beta web server through a reverse proxy or public tunnel. Before hosted deployment, add authentication, per-user authorization, durable audit logging, service-wide quotas, network-level private-destination blocking, explicit egress policy, isolated workers, and persistent-result retention/deletion controls. These hosted controls must be independent of the application-level DNS pinning and redirect rejection.

## Untrusted Input

Treat scanned repositories and archives as hostile. Robot Doctor performs static reads and does not build or execute repository code, ignores repository-owned scanner configuration for Git and upload intake, rejects ZIP links, unsafe paths, and every `.git` archive path component, and skips repository symlinks. HTTPS Git intake resolves DNS in an isolated process with a hard deadline and cancellation polling, terminates that process on cancellation or timeout, rejects any lookup containing a non-public address, pins Git/libcurl to the accepted addresses, disables proxies and redirects, ignores global/system Git configuration, and disallows file and external-helper transports. Git provenance subprocesses explicitly disable hooks, `core.fsmonitor`, and the untracked cache before reading revision metadata. ZIP results record both archive and extracted-content SHA-256 values. Private Git tokens are injected only into the clone subprocess environment and are not stored; nevertheless, use read-only, repository-scoped, short-lived tokens. Static parsing, DNS, Git, and TLS libraries may still contain defects; use least-privilege execution and avoid scanning secrets.
