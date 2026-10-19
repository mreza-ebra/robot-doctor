# Robot Doctor / FaciRob backlog

Updated 18 October 2026. Checked items are implemented or internally verified; they are not customer-validation claims.

## First-run and showcase milestone

- [x] Explain the practical ROS 2 analysis benefit before requesting a repository.
- [x] Add an offline bundled example with source evidence and a reviewed correction.
- [x] Offer real broken/corrected ZIPs that reproduce RD302 and its removal.
- [x] Put assessment, relevant findings, and next steps before detailed inventory.
- [x] Use project names, report-section links, and collapsed inventory tables.
- [x] Explain local ZIP analysis, Git network activity, retention, and static limits.
- [x] Reject ambiguous form submissions containing both Git and ZIP inputs.
- [x] Make missing-parameter guidance appropriate to Python/CMake packaging.
- [x] Export standalone linked HTML/JSON/source materials that open without installation.
- [x] Finish and verify the short screen walkthrough and transcript.
- [x] Prepare personalized invitation template, interview script, observation form, and initial decision criteria.
- [x] Complete browser, regression, accuracy, distribution, and installed-wheel checks; record results in docs/release_checklist.md.
- [x] Push the milestone and verify remote GitHub Actions: implementation `bbdee7f`, [successful run 39152309587](https://github.com/mreza-ebra/robot-doctor/actions/runs/39152309587).

## Before external pilots

- [x] Reproduce a real open public ROS 2 issue: FANUC driver #50 at pinned SHA `da5db3f`; improve the generic detector, prepare a local correction, retain before/after evidence, and document static verification limits.
- [x] Add a second real repository: MoveIt Resources #219 at pinned SHA `e1657c1`; use the existing detector, review a correction to local RViz assets, retain original/before/after scans, and provide a two-case demo chooser and CI replays.
- [x] Add a third recognized public repository: ROBOTIS TurtleBot3 #1149 at `fc817ce`; retain the initial miss, add generic informational launch-context detection, reproduce the existing upstream namespace correction locally, and include it in the three-case demo and CI replay.
- [ ] Verify TurtleBot3 standalone and parent launches in ROS, with omitted namespace and `namespace:=tb1`; `TURTLEBOT3_MODEL` remains required. No upstream submission is needed for reproduction; the reporter already opened PR #1150.
- [ ] Verify the MoveIt local RViz presets in ROS/RViz, including full and tutorial modes; static asset/install checks do not establish visual equivalence or successful launch.
- [ ] Submit the FANUC patch upstream only if explicitly requested; no issue comment or PR has been sent.

- [ ] Founder review of the showcase and evaluation offer.
- [ ] Confirm evaluation permission for selected participants under the current proprietary license; decide public distribution terms separately.
- [ ] Personalize invitations for three relevant ROS 2 engineers; obtain authorization before sending.
- [ ] Use the attached offline showcase, or explicitly approve and verify a public hosting destination before claiming a live demo URL.
- [ ] Run three observed evaluations and record unaided success, interventions, relevance, trust, and repeat-use requests.
- [ ] Check installation on participant machines; complete remaining Safari launcher verification.
- [ ] Test with screen-reader users and representative browser/OS combinations; internal checks are not accessibility certification.

## Next product decisions, driven by pilot evidence

- [ ] Choose the recurring problem/segment using actual incidents and useful findings.
- [ ] Improve false-alarm explanations and prioritization using engineer feedback.
- [ ] Evaluate saved project baselines, commit comparisons, and a CI report for repeat usage.
- [ ] Prototype a bounded read-only runtime snapshot importer (topics, QoS, parameters, TF, logs).
- [ ] Compare repository assumptions with observed runtime evidence, retaining ambiguity.
- [ ] Evaluate grounded assistance and reviewable patches only for supported fault classes.
- [ ] Add reproducible build/simulation/runtime verification where justified by pilots.
- [ ] Establish paid pilot scope, delivery effort, inference/hosting costs, and purchasing evidence.
- [ ] Expand the independent benchmark with permissioned real failure cases.

Hosted multi-user deployment, autonomous robot control, model training, hardware purchases, and broad robot-building workflows are not part of this milestone. Hosted work requires the controls in SECURITY.md and a separate implementation decision.
