#!/usr/bin/env python3
"""Reproduce MoveIt Resources #219 and correct the RViz paths in a separate copy.

Supply a pristine pinned checkout. No network, repository code, ROS launch,
upstream write, or robot operation is performed by this script.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from robot_doctor.public_case import CASES, case_directory, is_target
from robot_doctor.scanner import git_provenance, scan_repository
from robot_doctor.web import page, public_case_body, result_body

CASE = CASES["moveit"]
ISSUE_URL = CASE["repository_url"] + "/issues/219"
LAUNCH = "panda_moveit_config/launch/moveit_rviz.launch.py"
RVIZ_FILES = ("moveit.rviz", "moveit_empty.rviz")
REPLACEMENTS = (
    ('get_package_share_directory("moveit2_tutorials")', 'get_package_share_directory("moveit_resources_panda_moveit_config")'),
    ('"panda_moveit_config_demo.rviz"', '"moveit.rviz"'),
    ('"panda_moveit_config_demo_empty.rviz"', '"moveit_empty.rviz"'),
)


def corrected_launch(source: str) -> str:
    for old, new in REPLACEMENTS:
        if source.count(old) != 1:
            raise ValueError("the pinned launcher no longer has the expected RViz references")
        source = source.replace(old, new)
    ast.parse(source)
    return source


def check_local_rviz_paths(repository: Path, source: str) -> list[str]:
    """Check the two literal local assets and the exact upstream install rule."""
    package = repository / "panda_moveit_config"
    install_rule = "install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})"
    if install_rule not in (package / "CMakeLists.txt").read_text():
        raise ValueError("the launch directory is not covered by the expected install rule")
    for old, new in REPLACEMENTS:
        if old in source or source.count(new) != 1:
            raise ValueError("the corrected launcher does not use the reviewed local references")
    paths = [package / "launch" / name for name in RVIZ_FILES]
    if not all(path.is_file() and not path.is_symlink() and path.stat().st_size for path in paths):
        raise ValueError("both local RViz configuration files must exist")
    return [path.relative_to(repository).as_posix() for path in paths]


def portable(data: dict) -> dict:
    data["repository"] = {"name": "moveit_resources", "path": ".", "source": CASE["repository_url"] + ".git", "source_type": "local"}
    return data


def run(repository: Path, output: Path, *, initial_report: Path | None = None, corrected_output: Path | None = None) -> dict:
    provenance = git_provenance(repository)
    if provenance.get("commit_sha") != CASE["revision"] or provenance.get("dirty") is not False:
        raise ValueError("use a pristine checkout at the exact pinned revision; no input source is modified")
    launch = repository / LAUNCH
    if launch.is_symlink() or not launch.resolve().is_relative_to(repository.resolve()):
        raise ValueError("the launch file must be a regular file inside the checkout")
    original = launch.read_text(encoding="utf-8")
    replacement = corrected_launch(original)
    verified_paths = check_local_rviz_paths(repository, replacement)
    before = scan_repository(repository)
    targets = [item for item in before["diagnostics"] if is_target(item, "moveit")]
    if len(targets) != 1:
        raise ValueError("the unmodified repository must produce exactly one target finding")
    with tempfile.TemporaryDirectory(prefix="robot-doctor-moveit-case-") as temporary:
        corrected = corrected_output or Path(temporary) / "corrected"
        shutil.copytree(repository, corrected, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        (corrected / LAUNCH).write_text(replacement, encoding="utf-8")
        after = scan_repository(corrected)
        check_local_rviz_paths(corrected, replacement)
        if any(is_target(item, "moveit") for item in after["diagnostics"]):
            raise ValueError("the local path correction did not remove the target finding")
        signature = lambda data: Counter(json.dumps(item, sort_keys=True) for item in data["diagnostics"] if not is_target(item, "moveit"))
        if signature(before) != signature(after):
            raise ValueError("unrelated diagnostics changed; investigate before publishing")
        for name in ("package.xml", "CMakeLists.txt"):
            if (repository / "panda_moveit_config" / name).read_bytes() != (corrected / "panda_moveit_config" / name).read_bytes():
                raise ValueError("the reviewed correction must not change package dependencies or install rules")
    initial = before
    if initial_report:
        initial = json.loads(initial_report.read_text())
        if initial["provenance"]["git"]["commit_sha"] != CASE["revision"] or sum(is_target(item, "moveit") for item in initial["diagnostics"]) != 1:
            raise ValueError("the original-detector snapshot must match the pinned revision and finding")
    output.mkdir(parents=True, exist_ok=True)
    patch = "".join(difflib.unified_diff(original.splitlines(keepends=True), replacement.splitlines(keepends=True), fromfile="a/" + LAUNCH, tofile="b/" + LAUNCH))
    (output / "fix.patch").write_text(patch, encoding="utf-8")
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    for origin, name in (("panda_description/LICENSE", "panda-description-Apache-2.0.txt"), ("fanuc_description/LICENSE", "fanuc-description.txt"), ("fanuc_moveit_config/LICENSE", "fanuc-moveit-config.txt")):
        shutil.copyfile(repository / origin, licenses / name)
    (output / "THIRD_PARTY_NOTICES.md").write_text(
        "# Upstream attribution\n\nSource evidence: moveit/moveit_resources, commit " + CASE["revision"] + ".\n\n"
        "The Panda configuration package identifies Mike Lautman as author and maintainer and declares BSD in package.xml. That directory has no separate license text at the pinned revision; this notice does not invent one. The full scan also includes other packages: exact upstream license files from panda_description (Apache-2.0), fanuc_description, and fanuc_moveit_config are retained in licenses/. Source excerpts retain their upstream attribution and terms.\n\n"
        "The three-line patch is a local proposed correction. No upstream acceptance or endorsement is implied. Robot Doctor and its narrative retain their own license.\n", encoding="utf-8")
    for stage, data in (("initial", initial), ("before", before), ("after", after)):
        portable(data)
        (output / f"{stage}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if stage != "initial":
            intro = '<p><a href="index.html">Back to the MoveIt public issue case study</a></p><p>Only the external RViz configuration lookup in issue #219 was corrected. Other findings remain.</p>'
            body = result_body(data, interactive=False, download_links=((f"{stage}.json", "Full scan JSON"), ("fix.patch", "Reviewed patch")))
            (output / f"{stage}.html").write_text(page(f"MoveIt Resources · {stage} correction", intro + body, home_href="index.html", scan_href="index.html"), encoding="utf-8")
    case = {
        "case_id": "moveit", "kind": "issue-guided reproduction", "issue_url": ISSUE_URL,
        "issue_state_at_check": "OPEN", "issue_state_checked_on": "2026-10-18",
        "repository_url": CASE["repository_url"], "revision": CASE["revision"], "branch_at_capture": "ros2",
        "captured_at": datetime.now(timezone.utc).isoformat(), "package": CASE["package"], "dependency": CASE["dependency"],
        "launch_file": LAUNCH, "target_code": "RD101", "target_before": 1, "target_after": 0,
        "before_summary": before["summary"], "after_summary": after["summary"],
        "initial_detector_commit": "c89b4e3" if initial_report else None, "initial_detector_target_count": 1,
        "source_finding": targets[0], "verified_local_rviz_files": verified_paths,
        "original_launch_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "corrected_launch_sha256": hashlib.sha256(replacement.encode()).hexdigest(),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "checks": {"launch_python_syntax_valid": True, "both_local_rviz_files_exist": True, "launch_directory_install_rule_present": True, "manifest_unchanged": True, "only_target_diagnostic_removed": True, "build_run": False, "ros_runtime_run": False, "physical_robot_run": False, "upstream_submission": False},
    }
    (output / "case.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "index.html").write_text(page(CASE["page_title"], public_case_body(case), home_href="index.html", scan_href="index.html"), encoding="utf-8")
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/case_studies/moveit")
    parser.add_argument("--initial-report", type=Path)
    parser.add_argument("--corrected-output", type=Path, help="optional new directory retaining the corrected source")
    parser.add_argument("--bundle-in-app", action="store_true")
    args = parser.parse_args()
    case = run(args.repository.resolve(), args.output_dir, initial_report=args.initial_report, corrected_output=args.corrected_output)
    if args.bundle_in_app:
        shutil.copytree(args.output_dir, case_directory("moveit"), dirs_exist_ok=True)
    print(json.dumps({"revision": case["revision"], "target_before": 1, "target_after": 0, "output": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
