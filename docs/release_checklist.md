# Release Checklist

## 2026-10-18 First-Run And Showcase Milestone (0.6.0.dev0)

This is development work on the existing beta, not a new published release or a claim of customer validation.

- [x] Clear first-run value statement, offline example, and local-use explanation.
- [x] Real controlled RD302 diagnosis, reviewed one-line correction, and independently scanned corrected ZIP.
- [x] Findings, source evidence, next steps, project titles, and collapsible inventory in the local UI.
- [x] Standalone HTML/JSON/source showcase, sharing bundle, captioned 70-second screen walkthrough, and transcript.
- [x] Pilot invitation, observation script, note template, decision criteria, and backlog prepared; no outreach sent.
- [x] 49 unit tests, including six showcase regressions, pass locally on Python 3.13.6.
- [x] All seven scoped benchmark groups pass (121 labels); all six pinned repository/workspace regressions pass.
- [x] Live loopback HTTP smoke checks pass for examples, downloads, ZIP input, filters, input errors, CSRF, and Host validation.
- [x] GUI example comparison, source-link expansion, file-chooser ZIP scan, keyboard skip-link navigation, and 390 × 844 layout checked in the in-app browser.
- [x] Static export relative links checked; video duration, decoding, and representative rendered frames verified.
- [x] Final wheel/source-distribution contents and installed-wheel smoke checks.
- [x] Push implementation `bbdee7f` and verify [GitHub Actions run 39152309587](https://github.com/mreza-ebra/robot-doctor/actions/runs/39152309587): Python 3.10.15/3.13 units, pinned repositories, accuracy benchmark, packaging, live Git intake, and non-root container/HTTP workflow all pass.

Direct `file:` navigation is blocked by the automation browser, so saved-report disk opening was not visually tested there. The local app uses the same renderer. Screen-reader, Safari-launcher, physical robot, and external user evaluations remain open in BACKLOG.md. Public showcase hosting and participant evaluation permission remain founder decisions before outreach.

## Historical v0.5.0 Release

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
- [x] Verify the final macOS launcher manually in a current Chrome build.
- [ ] Verify the final macOS launcher manually in a current Safari build.

## Publication

- [x] Configure the official public Git remote: `https://github.com/mreza-ebra/robot-doctor.git`.
- [x] Push commit `3502e8b` to `origin/main`.
- [x] GitHub Actions run `29861027447` succeeds for commit `3502e8b`.
- [x] The non-root container build and live health-check job succeeds in run `29861027447`.
- [x] Push DNS cancellation, live intake, and Node.js 24 action commit `ed290bc` to `origin/main`.
- [x] GitHub Actions run `29862156154` succeeds for commit `ed290bc` without action runtime deprecation warnings.
- [x] Push Python 3.10.15 floor and terminable DNS resolver commit `aef5f2e` to `origin/main`.
- [x] GitHub Actions run `29865323582` succeeds for commit `aef5f2e`.
- [x] GitHub Actions run `29939796043` succeeds for release candidate `ee5e339`.
- [x] Create and push annotated tag `v0.5.0` at `ee5e339` after remote CI is green.
- [x] Publish the verified wheel and source distribution from tag `v0.5.0`.
- [x] Enable private vulnerability reporting on the official repository.

Robot Doctor `v0.5.0` was published on 2026-07-22 from commit `ee5e339` after GitHub Actions run `29939796043` passed unit tests at Python 3.10.15 and 3.13, package, real-repository, accuracy, generated-artifact, live DNS-pinned Git intake, non-root container-build, and live health-check jobs. The release includes verified wheel and source-distribution artifacts, and private vulnerability reporting is enabled. Remaining release work is Safari launcher verification.
