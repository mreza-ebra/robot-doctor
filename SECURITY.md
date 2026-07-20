# Security Policy

## Supported Versions

Robot Doctor is currently a local-only beta. Security fixes target the latest `0.5.x` release candidate; older snapshots are unsupported.

## Reporting A Vulnerability

Do not open a public issue containing exploit details. Once the official GitHub repository is configured, use its private vulnerability-reporting feature. Until then, report privately to the repository owner through the same trusted channel used to receive the software.

Include the affected version or commit, operating system, reproduction steps, impact, and whether untrusted repository content is required. Expect an acknowledgement within five business days; remediation timing depends on severity and reproducibility.

## Deployment Boundary

`robot-doctor-web` is designed for one user on a loopback interface. It rejects non-loopback binding and applies CSRF, Origin, Host, task-count, upload, extraction, checkout, traversal, and read limits. These controls do not make it a multi-user hosted service.

Do not expose the beta web server through a reverse proxy or public tunnel. Before hosted deployment, add authentication, per-user authorization, durable audit logging, service-wide quotas, DNS resolution checks, redirect-destination validation, network egress policy, isolated workers, and persistent-result retention/deletion controls.

## Untrusted Input

Treat scanned repositories and archives as hostile. Robot Doctor performs static reads and does not build or execute repository code, ignores repository-owned configuration for Git and upload intake, rejects ZIP links and unsafe paths, and skips repository symlinks. Static parsing and Git transport libraries may still contain defects; use least-privilege execution and avoid scanning secrets.
