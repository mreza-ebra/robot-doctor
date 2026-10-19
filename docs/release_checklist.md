# Release Checklist

## 2026-10-19 Third Public Issue Example (0.6.0.dev0)

- [x] Capture TurtleBot3 main revision `fc817ce3073af1d6032397c64504134882af5e9a`, open issue #1149 and existing open proposed fix #1150, and public adoption signals (2,078 stars / 1,188 forks; no clone counts).
- [x] Preserve the initial detector miss and add generic import-aware RD304 informational detection for launch configuration requirements; distinguish intended parent context from standalone use.
- [x] Prepare a separate namespace declaration/default correction; verify patch applicability, Python syntax, empty defaults and retained named binding, target removal, and identical unrelated diagnostics.
- [x] Bundle original/before/after evidence, patch, attribution and exact license; add a responsive three-case chooser and pinned CI replay while retaining existing URLs.
- [x] All 63 unit tests, 121 benchmark labels, and six repository/workspace regressions pass. All three local replays, wheel/sdist checks, installed-wheel module/asset comparison, live HTTP routes/downloads, browser chooser/evidence/correction checks, 815 offline asset/fragment links, and TurtleBot3 JSON schema validation pass. Regenerate the bundle with the final case pages and screenshots.
- [x] Push implementation `8e28505` and verify [GitHub Actions run 39296234962](https://github.com/mreza-ebra/robot-doctor/actions/runs/39296234962): all nine jobs pass, including independent FANUC/MoveIt/TurtleBot3 replays, Python 3.10.15/3.13 units, repository regressions/benchmark, packaging, live Git intake, and container/HTTP checks.

The TurtleBot3 approach was already proposed upstream by the reporter. This work makes no upstream submission and runs no ROS build/launch, simulation, or robot test. Other diagnostics, including an unrelated error, remain in its scan. The repository remains dependent on `TURTLEBOT3_MODEL`. Details: `docs/case_studies/turtlebot3/README.md`.

## 2026-10-18 Two Public Issue Examples (0.6.0.dev0)

- [x] Verify MoveIt Resources #219 is open and pin the current `ros2` revision to `e1657c128ea61621777a041e3783c9c7c93db4ca`.
- [x] Capture the existing detector's finding without a scanner change; prepare a three-line RViz-path correction in a separate copy, preserving pristine upstream source.
- [x] Verify patch applicability, Python syntax, both existing local RViz files and their install rule, unchanged manifest, target removal, and identical unrelated diagnostics.
- [x] Bundle provenance, original/before/after scans, HTML reports, patch, attribution, and upstream license texts.
- [x] Add a two-case chooser and case-specific routes/downloads to the app and offline showcase; retain original FANUC URLs and add both pinned CI replays.
- [x] All 57 unit tests pass; both pinned local replays, wheel/sdist contents, installed-wheel offline assets/renderers, live HTTP routes/downloads, and browser chooser/source-evidence/correction checks pass. Regenerate the showcase with both cases, return navigation, and screenshots; validate relative asset/fragment links across the exported HTML.
- [x] Push implementation `aad5b05` and verify [GitHub Actions run 39159744764](https://github.com/mreza-ebra/robot-doctor/actions/runs/39159744764): all eight jobs pass, including independent FANUC/MoveIt source replays, Python 3.10.15/3.13 units, repository regressions/benchmark, packaging, live Git intake, and container/HTTP checks.

No upstream submission, ROS build/launch, simulation, physical robot operation, or RViz visual comparison was performed. The local presets can differ from the tutorial presets previously referenced. Detailed evidence is in `docs/case_studies/moveit/README.md`.

## 2026-10-18 Public Issue Case Study (0.6.0.dev0)

- [x] Verify FANUC driver issue #50 is open and pin the observed current `main` source to `da5db3f611cdc9dffe687e056c4e79bea584bf5c`.
- [x] Preserve the original detector's miss; add a generic, import-aware literal package-share extractor without vendor-specific scanner rules.
- [x] Prepare a separate corrected source copy and a one-line runtime dependency patch; the pristine upstream checkout remains unchanged.
- [x] Verify the patch applies, XML parses, the local dependency closure gains the missing packages, the target finding disappears, and unrelated diagnostics are identical.
- [x] Preserve original and improved before/after JSON, full HTML reports, hashes, upstream notices/licenses, and replay instructions.
- [x] Add the offline public-case view, primary home-screen entry, showcase bundle evidence, and dedicated CI replay job.
- [x] All 54 unit tests pass; the 121-label benchmark, six pinned repository/workspace regressions, and existing 95-package diagnostic-noise totals remain unchanged.
- [x] Verify final wheel/sdist contents, installed-wheel offline case assets, browser source-evidence expansion and correction view, and live HTTP case downloads.
- [x] Push implementation `9269535` and verify [GitHub Actions run 39155060660](https://github.com/mreza-ebra/robot-doctor/actions/runs/39155060660): all seven jobs pass, including pinned FANUC replay, Python 3.10.15/3.13 units, repository regressions/benchmark, packaging, live Git intake, and container/HTTP checks.

This is an issue-guided reproduction, not blind discovery. No upstream comment/PR, ROS build/runtime, simulation, or physical robot operation has been performed. Detailed evidence and limitations are in `docs/case_studies/fanuc/README.md`.

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
