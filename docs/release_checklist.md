# Release Checklist

## Source And Version

- [x] `pyproject.toml` and `robot_doctor.__version__` report `0.5.0`.
- [x] `CHANGELOG.md` contains the `0.5.0` release notes.
- [x] `SECURITY.md` documents reporting and deployment boundaries.
- [x] Generated TurtleBot JSON and three overview reports match the release scanner.

## Quality Gates

- [x] Python compilation and unit tests pass.
- [x] Both JSON schemas validate formally.
- [x] The 121-label benchmark passes at required precision and recall.
- [x] Six pinned real-repository/workspace regressions pass with zero error diagnostics.
- [x] Wheel and source distribution build, wheel contents, installed commands, and loopback HTTP smoke tests pass.
- [x] Malicious ZIP `.git/config` and Git `core.fsmonitor` execution regressions pass.
- [x] Local in-app browser upload, topology, filters, and provenance hashes pass end to end.
- [x] Docker image declares fixed non-root UID/GID `10001:10001`; local tests enforce the image and Compose configuration.
- [x] DNS lookup timeout and cancellation regressions pass locally, and timed-out resolver processes are terminated.
- [x] A real DNS-pinned HTTPS clone passes locally and in the dedicated CI live-intake job.
- [ ] Verify the final macOS launcher manually in current Safari and Chrome builds.

## Publication

- [x] Configure the official public Git remote: `https://github.com/mreza-ebra/robot-doctor.git`.
- [x] Push commit `3502e8b` to `origin/main`.
- [x] GitHub Actions run `29861027447` succeeds for commit `3502e8b`.
- [x] The non-root container build and live health-check job succeeds in run `29861027447`.
- [x] Push DNS cancellation, live intake, and Node.js 24 action commit `ed290bc` to `origin/main`.
- [x] GitHub Actions run `29862156154` succeeds for commit `ed290bc` without action runtime deprecation warnings.
- [x] Push Python 3.10.15 floor and terminable DNS resolver commit `aef5f2e` to `origin/main`.
- [x] GitHub Actions run `29865323582` succeeds for commit `aef5f2e`.
- [ ] Create and push annotated tag `v0.5.0` only after remote CI is green.
- [ ] Publish release artifacts generated from that tag.
- [x] Enable private vulnerability reporting on the official repository.

GitHub Actions run `29865323582` completed successfully on 2026-07-21 for commit `aef5f2e`; unit tests at Python 3.10.15 and 3.13, package, real-repository, accuracy, generated-artifact, live DNS-pinned Git intake, non-root container-build, and live health-check jobs all passed. Private vulnerability reporting is enabled. Remaining release work is Safari/Chrome launcher verification, tagging, and release artifacts.
