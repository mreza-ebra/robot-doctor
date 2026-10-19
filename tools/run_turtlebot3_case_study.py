#!/usr/bin/env python3
"""Replay TurtleBot3 #1149 using a pristine pinned checkout and a separate fix.

No network, repository-code execution, ROS build/launch, or upstream write.
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

CASE = CASES["turtlebot3"]
LAUNCH = CASE["launch_file"]
ORIGINAL = "namespace = LaunchConfiguration('namespace')"
CORRECTED = "namespace = LaunchConfiguration('namespace', default='')"
ANCHOR = "    return LaunchDescription([\n"
DECLARATION = "        DeclareLaunchArgument(\n            'namespace',\n            default_value='',\n            description='Robot namespace'),\n"


def corrected_launch(source: str) -> str:
    if source.count(ORIGINAL) != 1 or source.count(ANCHOR) != 1:
        raise ValueError("the pinned launcher no longer matches the reviewed missing-argument case")
    corrected = source.replace(ORIGINAL, CORRECTED).replace(ANCHOR, ANCHOR + DECLARATION)
    check_namespace_contract(corrected)
    return corrected


def check_namespace_contract(source: str) -> None:
    """Inspect syntax/bindings only; do not import or execute the ROS launcher."""
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    lookups = [node for node in calls if node.func.id == "LaunchConfiguration" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "namespace"]
    declarations = [node for node in calls if node.func.id == "DeclareLaunchArgument" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "namespace"]
    if len(lookups) != 1 or len(declarations) != 1:
        raise ValueError("the correction must retain one named lookup and add one declaration")
    for node, key in ((lookups[0], "default"), (declarations[0], "default_value")):
        defaults = [item.value for item in node.keywords if item.arg == key]
        if len(defaults) != 1 or not isinstance(defaults[0], ast.Constant) or defaults[0].value != "":
            raise ValueError("both namespace defaults must be the empty string")
    if not any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "namespace" for target in node.targets) and node.value is lookups[0] for node in ast.walk(tree)):
        raise ValueError("the namespace variable must remain bound to the named substitution")


def portable(data: dict) -> dict:
    data["repository"] = {"name": "turtlebot3", "path": ".", "source": CASE["repository_url"] + ".git", "source_type": "local"}
    return data


def run(repository: Path, output: Path, *, initial_report: Path | None = None, corrected_output: Path | None = None) -> dict:
    provenance = git_provenance(repository)
    if provenance.get("commit_sha") != CASE["revision"] or provenance.get("dirty") is not False:
        raise ValueError("use a pristine checkout at the exact pinned revision")
    if output.resolve().is_relative_to(repository.resolve()) or (corrected_output and corrected_output.resolve().is_relative_to(repository.resolve())):
        raise ValueError("case evidence and corrected source must be outside the pristine checkout")
    launch = repository / LAUNCH
    if launch.is_symlink() or not launch.resolve().is_relative_to(repository.resolve()):
        raise ValueError("the launch file must be a regular file inside the checkout")
    original = launch.read_text(encoding="utf-8")
    replacement = corrected_launch(original)
    before = scan_repository(repository)
    targets = [item for item in before["diagnostics"] if is_target(item, "turtlebot3")]
    if len(targets) != 1:
        raise ValueError("the unchanged source must produce exactly one target finding")
    with tempfile.TemporaryDirectory(prefix="robot-doctor-turtlebot3-case-") as temporary:
        corrected = corrected_output or Path(temporary) / "corrected"
        shutil.copytree(repository, corrected, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        (corrected / LAUNCH).write_text(replacement, encoding="utf-8")
        after = scan_repository(corrected)
        if any(is_target(item, "turtlebot3") for item in after["diagnostics"]):
            raise ValueError("the local correction did not remove the target finding")
        signature = lambda data: Counter(json.dumps(item, sort_keys=True) for item in data["diagnostics"] if not is_target(item, "turtlebot3"))
        if signature(before) != signature(after):
            raise ValueError("unrelated diagnostics changed; investigate before publishing")
    initial = None
    if initial_report:
        initial = json.loads(initial_report.read_text())
        if initial["provenance"]["git"]["commit_sha"] != CASE["revision"] or any(is_target(item, "turtlebot3") for item in initial["diagnostics"]):
            raise ValueError("the original-detector snapshot must match the pinned revision and miss")
    output.mkdir(parents=True, exist_ok=True)
    patch = "".join(difflib.unified_diff(original.splitlines(keepends=True), replacement.splitlines(keepends=True), fromfile="a/" + LAUNCH, tofile="b/" + LAUNCH))
    (output / "fix.patch").write_text(patch, encoding="utf-8")
    (output / "licenses").mkdir(exist_ok=True)
    shutil.copyfile(repository / "LICENSE", output / "licenses/Apache-2.0.txt")
    (output / "THIRD_PARTY_NOTICES.md").write_text(
        "# Upstream attribution\n\nSource evidence: ROBOTIS-GIT/turtlebot3, commit " + CASE["revision"] + ".\n\n"
        "Copyright 2019 ROBOTIS CO., LTD. The affected launch file names Darby Lim as author and is licensed under Apache-2.0. The exact upstream root license is preserved in licenses/Apache-2.0.txt; other source excerpts retain their upstream headers and terms.\n\n"
        "The issue reporter already supplied a proposed fix in upstream PR #1150. This is a local issue-guided reproduction, not an original discovery, upstream submission, or endorsement. Robot Doctor and its narrative retain their own license.\n", encoding="utf-8")
    stages = {"before": before, "after": after}
    if initial is not None:
        stages["initial"] = initial
    for stage, data in stages.items():
        portable(data)
        (output / f"{stage}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if stage != "initial":
            intro = '<p><a href="index.html">Back to the TurtleBot3 public issue case study</a></p><p>Only the namespace declaration/default in issue #1149 was corrected. Other diagnostics, including an unrelated error, remain.</p>'
            body = result_body(data, interactive=False, download_links=((f"{stage}.json", "Full scan JSON"), ("fix.patch", "Reviewed patch")))
            (output / f"{stage}.html").write_text(page(f"TurtleBot3 · {stage} correction", intro + body, home_href="index.html", scan_href="index.html"), encoding="utf-8")
    case = {
        "case_id": "turtlebot3", "kind": "issue-guided reproduction",
        "issue_url": CASE["repository_url"] + "/issues/1149", "existing_upstream_pr_url": CASE["repository_url"] + "/pull/1150",
        "issue_state_at_check": "OPEN", "existing_upstream_pr_state_at_check": "OPEN", "issue_state_checked_on": "2026-10-18",
        "repository_url": CASE["repository_url"], "revision": CASE["revision"], "branch_at_capture": "main",
        "captured_at": datetime.now(timezone.utc).isoformat(), "package": CASE["package"], "launch_file": LAUNCH,
        "target_code": "RD304", "launch_configuration": "namespace", "target_before": 1, "target_after": 0,
        "before_summary": before["summary"], "after_summary": after["summary"], "source_finding": targets[0],
        "initial_detector_commit": "48cb3c1" if initial else None, "initial_detector_target_count": 0 if initial else None,
        "adoption": {"stars": 2078, "forks": 1188, "checked_on": "2026-10-18", "source": "https://api.github.com/repos/ROBOTIS-GIT/turtlebot3", "clone_count": None},
        "original_launch_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "corrected_launch_sha256": hashlib.sha256(replacement.encode()).hexdigest(), "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "checks": {"launch_python_syntax_valid": True, "namespace_default_empty": True, "namespace_declaration_default_empty": True, "named_override_binding_preserved": True, "only_target_diagnostic_removed": True, "build_run": False, "ros_runtime_run": False, "physical_robot_run": False, "upstream_submission": False},
    }
    (output / "case.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "index.html").write_text(page(CASE["page_title"], public_case_body(case), home_href="index.html", scan_href="index.html"), encoding="utf-8")
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/case_studies/turtlebot3")
    parser.add_argument("--initial-report", type=Path)
    parser.add_argument("--corrected-output", type=Path, help="optional new directory retaining the corrected source")
    parser.add_argument("--bundle-in-app", action="store_true")
    args = parser.parse_args()
    case = run(args.repository.resolve(), args.output_dir, initial_report=args.initial_report, corrected_output=args.corrected_output)
    if args.bundle_in_app:
        shutil.copytree(args.output_dir, case_directory("turtlebot3"), dirs_exist_ok=True)
    print(json.dumps({"revision": case["revision"], "target_before": 1, "target_after": 0, "output": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
