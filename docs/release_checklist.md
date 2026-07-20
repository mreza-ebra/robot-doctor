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

## Publication

- [ ] Configure the official Git remote.
- [ ] Push the release commit and confirm GitHub Actions succeeds remotely.
- [ ] Create and push annotated tag `v0.5.0` only after remote CI is green.
- [ ] Publish release artifacts generated from that tag.
- [ ] Enable private vulnerability reporting on the official repository.

The unchecked publication steps require the official repository URL and cannot be truthfully completed in an unconnected local repository.
