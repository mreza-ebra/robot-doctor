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
- [ ] Verify the final macOS launcher manually in current Safari and Chrome builds.

## Publication

- [x] Configure the official public Git remote: `https://github.com/mreza-ebra/robot-doctor.git`.
- [x] Push commit `7a79bf3` to `origin/main`.
- [ ] Push the current release candidate and confirm GitHub Actions succeeds remotely.
- [ ] Confirm the new container build and live health-check job succeeds remotely.
- [ ] Create and push annotated tag `v0.5.0` only after remote CI is green.
- [ ] Publish release artifacts generated from that tag.
- [ ] Enable private vulnerability reporting on the official repository.

GitHub Actions run `29850645883` completed on 2026-07-21. Unit tests and packaging passed, while the combined real-repository job failed because TurtleBot 4 was cloned inside a workspace whose expected counts intentionally excluded it. The checkout layout is corrected in the current working tree, but remote CI remains unchecked until these changes are committed and pushed.
