# FaciRob / Robot Doctor working showcase

Generated and reviewed on 18 October 2026 using the current analyzer. The source example is synthetic and contains a deliberately introduced missing-parameter-file reference. No customer results, time savings, build success, or runtime behavior are claimed.

## Open and explore

Download and extract [the complete bundle](../FaciRob_Showcase.zip), then open `showcase/index.html` in your normal browser. Alternatively, open `docs/showcase/index.html` from the repository checkout. All report links are relative and there are no external scripts, fonts, or services. No installation is required to inspect the saved showcase. GitHub's HTML file view displays source rather than running the page.

The local application offers the same example at `/example` after starting `robot-doctor-web`. Evaluating your own project requires the local Python or Docker installation described in the repository README.

## Walkthrough and transcript

[Watch the captioned 70-second walkthrough](walkthrough.mp4). Captions are burned into the video; [SRT captions](walkthrough.srt) are also supplied. The video consists of actual browser captures edited for pacing, rather than a continuous real-time recording. It has no narration.

| Time | What is shown | Transcript |
| --- | --- | --- |
| 00:00–00:10 | Revised entry screen | FaciRob maps ROS 2 projects and flags potential integration issues without building the repository. |
| 00:10–00:20 | Built-in example | Start with an offline example. The missing YAML reference is deliberately introduced. |
| 00:20–00:30 | Source-backed finding | RD302 identifies the missing parameter file. Inspect source evidence and the repair guidance. |
| 00:30–00:40 | Architecture view | Explore source definitions and launch instances. This static map does not establish runtime behavior. |
| 00:40–00:50 | Reviewed correction | Review the supplied one-line correction: missing.yaml becomes scan.yaml. |
| 00:50–01:00 | Corrected assessment | The corrected source is scanned separately. RD302 is absent; build and runtime checks remain separate. |
| 01:00–01:10 | Local evaluation | Try a public Git URL or a local repository ZIP. Analysis runs locally and does not execute project code. |

## Reproduce the comparison

1. Inspect [the broken report](broken.html) and [its JSON](broken.json).
2. Download and extract [broken.zip](broken.zip) and [fixed.zip](fixed.zip) into separate directories.
3. Run `robot-doctor-scan /path/to/broken --json --output broken-scan.json` and repeat for the fixed directory.
4. Confirm the broken scan contains RD302 for `scan_demo/launch/scan.launch.py`, while the corrected scan does not.
5. Compare the source files: the only change is `"missing.yaml"` to `"scan.yaml"` in the launch reference.

The example contains a synthetic LaserScan publisher and observer, not a navigation system or physical sensor. Downloaded example READMEs describe separate optional ROS build/runtime checks, which have not been performed for this milestone. The root proprietary license also applies to the example source.

Regenerate reports and the sharing bundle:

```bash
python3 tools/build_showcase.py --bundle
```

To rebuild the walkthrough after recording fresh screenshots, install an ffmpeg binary with drawtext support (or the optional `imageio-ffmpeg` Python package), then run:

```bash
python3 tools/build_walkthrough.py
python3 tools/build_showcase.py --bundle
```

The app itself does not require the video tooling. The seven numbered screenshots in `screens/` are the accepted walkthrough captures. `qa-*` captures document internal keyboard and narrow-viewport checks and are excluded from the sharing bundle.

## Internal review

- Entry screen: the benefit and example are visible before repository input.
- Example: both stages use real scans, not hand-written findings; ZIPs reproduce the comparison.
- Results: findings and next actions precede the full inventory; source links reveal the relevant excerpt.
- Keyboard: the skip link receives visible focus, moves into main content, and leads to the example using Tab/Enter.
- Narrow viewport: entry and finding views were inspected at 390 × 844. Document width did not exceed the viewport; long tables remain individually scrollable.
- GUI upload: the bundled broken ZIP was selected through the file chooser and scanned to RD302 with its filename as the page title.
- HTTP checks: report downloads, filters, ZIP upload, invalid-stage rejection, ambiguous-input rejection, CSRF, and loopback Host validation passed.

The automation browser blocks `file:` navigation, so direct disk opening was not visually tested there. Saved HTML was generated with the same renderer as the visually checked app and its relative links were checked separately. No screen-reader evaluation, Safari launcher evaluation, physical robot test, or external user study has been completed. These checks do not establish full accessibility compliance or customer value.
