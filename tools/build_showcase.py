#!/usr/bin/env python3
"""Export real example scans as a self-contained, offline HTML showcase."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from robot_doctor.demo import example_zip, write_example
from robot_doctor.scanner import scan_repository
from robot_doctor.public_case import CASES, case_directory
from robot_doctor.web import example_intro, page, public_case_body, public_cases_body, result_body


def build_showcase(output: Path) -> dict[str, dict]:
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    with tempfile.TemporaryDirectory(prefix="robot-doctor-showcase-") as temporary:
        for stage in ("broken", "fixed"):
            repository = Path(temporary) / stage
            write_example(repository, stage)
            data = scan_repository(repository)
            # Export portable source identities rather than a developer's temp path.
            data["repository"] = {"name": "ROS 2 scan example", "path": ".", "source": "Bundled controlled example", "source_type": "local"}
            results[stage] = data
            (output / f"{stage}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (output / f"{stage}.zip").write_bytes(example_zip(stage))
            intro = example_intro(stage, broken_href="broken.html", fixed_href="fixed.html", zip_href=f"{stage}.zip")
            report = result_body(data, interactive=False, download_links=((f"{stage}.json", "Scan JSON"), (f"{stage}.zip", "Example source ZIP")))
            (output / f"{stage}.html").write_text(page("ROS 2 scan example", intro + report, home_href="index.html", scan_href="index.html#try-locally"), encoding="utf-8")
    broken_codes = [item["code"] for item in results["broken"]["diagnostics"]]
    fixed_codes = [item["code"] for item in results["fixed"]["diagnostics"]]
    if broken_codes != ["RD302"] or fixed_codes:
        raise RuntimeError(f"example no longer demonstrates the intended single-fault comparison: {broken_codes}, {fixed_codes}")
    case_hrefs = {}
    for case_id, case in CASES.items():
        destination = output / case["showcase_folder"]
        shutil.copytree(case_directory(case_id), destination, dirs_exist_ok=True)
        captured = json.loads((destination / "case.json").read_text())
        navigation = '<p><a href="../index.html">Choose another public issue case</a></p>'
        (destination / "index.html").write_text(page(case["page_title"], navigation + public_case_body(captured), home_href="../index.html", scan_href="../index.html#try-locally"), encoding="utf-8")
        case_hrefs[case_id] = case["showcase_folder"] + "/index.html"
    introduction = public_cases_body(case_hrefs) + '''
<h2>Controlled tutorial</h2>
<p class="lead">Trace a ROS 2 integration issue from a launch reference to source evidence, then inspect a corrected scan.</p>
<p>These reports come from the current Robot Doctor analyzer. Open this folder locally in a browser; no server, account, ROS installation, or repository access is required to explore the saved reports.</p>
<div class="actions"><a class="button" href="broken.html">Explore the intentional fault</a><a class="button secondary" href="fixed.html">Compare the corrected scan</a></div>
<ol class="steps"><li><strong>Inspect a real static finding</strong><p>The example intentionally refers to a missing YAML file.</p></li><li><strong>Trace the source evidence</strong><p>Review RD302 and the launch statement that produced it.</p></li><li><strong>Compare a reviewed correction</strong><p>Only the filename changes. The corrected scan no longer reports RD302.</p></li></ol>
<section class="panel"><h2>What this demonstrates</h2><p>Source analysis, evidence-backed diagnosis, architecture extraction, and static re-verification of a deliberately introduced fault.</p><p>It does not establish time savings, customer traction, build success, runtime communication, or hardware compatibility. This synthetic scan publisher and observer are not a navigation stack.</p></section>
<section class="panel" id="try-locally"><h2>Try your own project locally</h2><p>Install the local beta from the repository using Docker or Python. Once it is running, choose a repository ZIP or HTTPS Git URL.</p><pre><code>python3 -m pip install -e .
robot-doctor-web</code></pre><p>Python 3.10.15 or newer is required. ROS and colcon are not required for static scans. Docker is an alternative installation path. The analyzer does not execute repository code or send ZIP contents to a cloud analysis service.</p><p>The live application offers filters and downloadable reports. Results disappear when the local app closes unless downloaded. Review the repository README and LICENSE before installation or evaluation.</p></section>'''
    if (output / "walkthrough.mp4").is_file():
        introduction += '''<section class="panel"><h2>Watch the 70-second controlled-example walkthrough</h2><p>Actual interface captures of the synthetic tutorial, edited for pacing, with captions. This is not footage of either public case or a real-time performance benchmark.</p><video controls preload="metadata" style="width:min(100%,540px);height:auto" aria-label="Captioned Robot Doctor controlled-example walkthrough"><source src="walkthrough.mp4" type="video/mp4">Download the MP4 to watch the walkthrough.</video><p><a href="walkthrough.mp4">Download the video</a> · <a href="README.md">Read the transcript and reproduction steps</a></p></section>'''
    (output / "index.html").write_text(page("Understand a ROS 2 project before changing it", introduction, home_href="index.html", scan_href="#try-locally"), encoding="utf-8")
    return results


def bundle_showcase(output: Path) -> Path:
    target = output.parent / "FaciRob_Showcase.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.startswith("qa-"):
                archive.write(path, "showcase/" + path.relative_to(output).as_posix())
        archive.write(ROOT / "LICENSE", "showcase/LICENSE")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "showcase")
    parser.add_argument("--bundle", action="store_true", help="also package the complete showcase folder for offline sharing")
    args = parser.parse_args()
    build_showcase(args.output_dir)
    print(f"Showcase generated: {args.output_dir / 'index.html'}")
    if args.bundle:
        print(f"Showcase bundle: {bundle_showcase(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
