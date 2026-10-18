#!/usr/bin/env python3
"""Scan a pinned upstream repository, correct one manifest locally, and rescan.

No repository code is executed. No network calls, upstream writes, or hardware
operations are performed. Supply an existing pristine checkout at the pinned SHA.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from robot_doctor.public_case import DEPENDENCY, ISSUE_URL, MANIFEST, PACKAGE, REPOSITORY_URL, REVISION, case_directory, is_target
from robot_doctor.scanner import git_provenance, scan_repository
from robot_doctor.web import page, public_case_body, result_body


def dependency_names(path: Path) -> set[str]:
    tree = ET.parse(path)
    return {node.text.strip() for node in tree.getroot() if (node.tag == "depend" or node.tag.endswith("_depend")) and node.text}


def corrected_manifest(original: str) -> str:
    anchor = "  <exec_depend>fanuc_crx_description</exec_depend>\n"
    if original.count(anchor) != 1 or DEPENDENCY in dependency_names_from_text(original):
        raise ValueError("the pinned manifest no longer has the expected missing dependency")
    return original.replace(anchor, anchor + f"  <exec_depend>{DEPENDENCY}</exec_depend>\n")


def dependency_names_from_text(text: str) -> set[str]:
    return {node.text.strip() for node in ET.fromstring(text) if (node.tag == "depend" or node.tag.endswith("_depend")) and node.text}


def local_dependency_graph(repository: Path) -> dict[str, set[str]]:
    graph = {}
    for path in repository.rglob("package.xml"):
        if path.is_symlink():
            continue
        root = ET.parse(path).getroot()
        graph[root.findtext("name")] = dependency_names(path)
    return graph


def local_closure(graph: dict[str, set[str]]) -> list[str]:
    pending, seen = [PACKAGE], set()
    while pending:
        name = pending.pop()
        if name not in seen and name in graph:
            seen.add(name)
            pending.extend(graph[name])
    return sorted(seen)


def check_no_new_cycle(repository: Path) -> None:
    graph = local_dependency_graph(repository)
    pending, seen = [DEPENDENCY], set()
    while pending:
        name = pending.pop()
        if name == PACKAGE:
            raise ValueError("adding this dependency would create a local package dependency cycle")
        if name not in seen:
            seen.add(name)
            pending.extend(graph.get(name, set()))


def portable(data: dict) -> dict:
    data["repository"] = {"name": "fanuc_driver", "path": ".", "source": REPOSITORY_URL + ".git", "source_type": "local"}
    return data


def run(repository: Path, output: Path, *, initial_report: Path | None = None, corrected_output: Path | None = None) -> dict:
    provenance = git_provenance(repository)
    if provenance.get("commit_sha") != REVISION or provenance.get("dirty") is not False:
        raise ValueError("use a pristine checkout at the exact pinned revision; no input source is modified")
    manifest = repository / MANIFEST
    if manifest.is_symlink() or not manifest.resolve().is_relative_to(repository.resolve()):
        raise ValueError("the manifest must be a regular file inside the checkout")
    original = manifest.read_text(encoding="utf-8")
    replacement = corrected_manifest(original)
    check_no_new_cycle(repository)
    selection_before = local_closure(local_dependency_graph(repository))
    before = scan_repository(repository)
    target = [item for item in before["diagnostics"] if is_target(item)]
    if len(target) != 1:
        raise ValueError("the unmodified repository must produce exactly one target finding")
    with tempfile.TemporaryDirectory(prefix="robot-doctor-public-case-") as temporary:
        corrected = corrected_output or Path(temporary) / "corrected"
        shutil.copytree(repository, corrected, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        (corrected / MANIFEST).write_text(replacement, encoding="utf-8")
        after = scan_repository(corrected)
        selection_after = local_closure(local_dependency_graph(corrected))
        if DEPENDENCY in selection_before or DEPENDENCY not in selection_after:
            raise ValueError("manifest-derived local package selection does not demonstrate the reported omission")
        if any(is_target(item) for item in after["diagnostics"]):
            raise ValueError("the reviewed manifest change did not remove the target finding")
        signatures = lambda data: Counter(json.dumps(item, sort_keys=True) for item in data["diagnostics"] if not is_target(item))
        if signatures(before) != signatures(after):
            raise ValueError("unrelated diagnostics changed; investigate before publishing this comparison")
    initial = None
    if initial_report:
        initial = json.loads(initial_report.read_text())
        if initial["provenance"]["git"]["commit_sha"] != REVISION or any(is_target(item) for item in initial["diagnostics"]):
            raise ValueError("original-detector snapshot does not match the claimed revision/miss")
    output.mkdir(parents=True, exist_ok=True)
    patch = "".join(difflib.unified_diff(original.splitlines(keepends=True), replacement.splitlines(keepends=True), fromfile="a/" + MANIFEST, tofile="b/" + MANIFEST))
    (output / "fix.patch").write_text(patch, encoding="utf-8")
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("Apache-2.0.txt", "BSD-3-Clause.txt", "BSD-2-Clause.txt", "MIT.txt"):
        shutil.copyfile(repository / "LICENSES" / name, licenses / name)
    (output / "THIRD_PARTY_NOTICES.md").write_text("# Upstream attribution\n\nSource evidence: FANUC-CORPORATION/fanuc_driver, commit " + REVISION + ".\n\nCopyright (C) 2025–2026 FANUC America Corporation and FANUC CORPORATION. Original driver source is Apache-2.0. The upstream repository also contains sockpp (BSD-3-Clause), readwriterqueue (BSD-2-Clause), reflect-cpp and yaml-cpp (MIT). License texts are retained in licenses/.\n\nThe patch is a local proposed manifest correction, not an upstream-approved change. Robot Doctor and its case-study narrative retain their own license; upstream source attribution and licenses are unchanged.\n", encoding="utf-8")
    artifacts = {"before": before, "after": after}
    if initial is not None:
        artifacts["initial"] = initial
    for stage, data in artifacts.items():
        portable(data)
        (output / f"{stage}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if stage != "initial":
            body = '<p><a href="index.html">Back to the public issue case study</a></p><p>This is a full-repository scan. Only the manifest dependency named in issue #50 was corrected; other findings remain.</p>'
            body += result_body(data, interactive=False, download_links=((f"{stage}.json", "Full scan JSON"), ("fix.patch", "Reviewed patch")))
            (output / f"{stage}.html").write_text(page(f"FANUC driver · {stage} correction", body, home_href="index.html", scan_href="index.html"), encoding="utf-8")
    case = {
        "kind": "issue-guided reproduction",
        "issue_url": ISSUE_URL,
        "issue_state_at_check": "OPEN",
        "issue_state_checked_on": "2026-10-18",
        "repository_url": REPOSITORY_URL,
        "revision": REVISION,
        "branch_at_capture": "main",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "package": PACKAGE,
        "dependency": DEPENDENCY,
        "manifest": MANIFEST,
        "target_code": "RD101",
        "target_before": 1,
        "target_after": 0,
        "local_package_selection_before": selection_before,
        "local_package_selection_after": selection_after,
        "before_summary": before["summary"],
        "after_summary": after["summary"],
        "initial_detector_commit": "4bb7afba020c8315245ea7532f51762f878942fe" if initial else None,
        "initial_detector_target_count": 0 if initial else None,
        "original_manifest_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "corrected_manifest_sha256": hashlib.sha256(replacement.encode()).hexdigest(),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "source_finding": target[0],
        "checks": {"manifest_xml_valid": True, "no_new_local_dependency_cycle": True, "only_target_diagnostic_removed": True, "build_run": False, "ros_runtime_run": False, "physical_robot_run": False, "upstream_submission": False},
    }
    (output / "case.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "index.html").write_text(page("A real FANUC driver dependency issue", public_case_body(case), home_href="index.html", scan_href="index.html"), encoding="utf-8")
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/case_studies/fanuc")
    parser.add_argument("--initial-report", type=Path)
    parser.add_argument("--corrected-output", type=Path, help="optional new directory retaining the corrected source copy")
    parser.add_argument("--bundle-in-app", action="store_true", help="also copy saved evidence into the app's offline case-study assets")
    args = parser.parse_args()
    case = run(args.repository.resolve(), args.output_dir, initial_report=args.initial_report, corrected_output=args.corrected_output)
    if args.bundle_in_app:
        shutil.copytree(args.output_dir, case_directory(), dirs_exist_ok=True)
    print(json.dumps({"revision": case["revision"], "target_before": case["target_before"], "target_after": case["target_after"], "output": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
