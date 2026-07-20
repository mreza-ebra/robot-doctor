from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).parents[1]
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor.scanner import scan_repository

NAME_ONLY_CATEGORIES = {"executables", "node_names", "declared_parameters"}
LAUNCH_CATEGORY_FIELDS = {
    "launch_files": ("file", "format"),
    "launch_actions": ("file", "kind", "package", "executable", "name", "namespace", "value"),
    "launch_includes": ("file", "target", "resolved_path", "exists"),
    "launch_arguments": ("file", "name", "default"),
}
DIAGNOSTIC_FIELDS = ("code", "topic", "interface", "launch_file", "package", "dependency", "frame")


def normalized_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().replace("::", "/").replace(".", "/")
    return re.sub(r"/+", "/", normalized).strip("/")


def entity_key(category: str, item: dict[str, Any], *, compare_resolution: bool = False) -> tuple[Any, ...]:
    base = (item.get("package"), item.get("name"))
    key = base if category in NAME_ONLY_CATEGORIES else base + (normalized_type(item.get("type")),)
    return key + (item.get("resolved", True),) if compare_resolution else key


def finding_in_scope(package: str, item: dict[str, Any], scope: dict[str, Any]) -> bool:
    packages = set(scope.get("packages", []))
    files = set(scope.get("files", []))
    if packages and package not in packages:
        return False
    if files and not any(record.get("file") in files for record in item.get("evidence", [])):
        return False
    return True


def detected_entities(
    data: dict[str, Any],
    category: str,
    *,
    scope: dict[str, Any] | None = None,
    include_unresolved: bool = False,
    compare_resolution: bool = False,
) -> set[tuple[Any, ...]]:
    scope = scope or {}
    return {
        entity_key(
            category,
            {"package": report["package"]["name"], **item},
            compare_resolution=compare_resolution,
        )
        for report in data["packages"]
        for item in report[category]
        if (include_unresolved or item.get("resolved", True))
        and finding_in_scope(report["package"]["name"], item, scope)
    }


def record_key(item: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(item.get(field) for field in fields)


def detected_launch_records(data: dict[str, Any], category: str) -> set[tuple[Any, ...]]:
    records = []
    for launch in data["launch_graph"]["files"]:
        if category == "launch_files":
            records.append(launch)
            continue
        child_key = {
            "launch_actions": "actions",
            "launch_includes": "includes",
            "launch_arguments": "arguments",
        }[category]
        records.extend({"file": launch["file"], **item} for item in launch[child_key])
    return {record_key(item, LAUNCH_CATEGORY_FIELDS[category]) for item in records}


def detected_diagnostics(data: dict[str, Any], codes: set[str]) -> set[tuple[Any, ...]]:
    return {
        record_key(item, DIAGNOSTIC_FIELDS)
        for item in data["diagnostics"]
        if not codes or item["code"] in codes
    }


def git_revision(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def category_score(expected: set[tuple[Any, ...]], detected: set[tuple[Any, ...]]) -> dict[str, Any]:
    sort_key = lambda key: tuple("" if value is None else str(value) for value in key)
    return {
        "true_positive": len(expected & detected),
        "false_positive": len(detected - expected),
        "false_negative": len(expected - detected),
        "unexpected": [list(item) for item in sorted(detected - expected, key=sort_key)],
        "missing": [list(item) for item in sorted(expected - detected, key=sort_key)],
    }


def score_benchmark(benchmark: dict[str, Any], path: Path) -> dict[str, Any]:
    data = scan_repository(path)
    category_scores = {}
    scope = benchmark.get("scope", {})
    include_unresolved = benchmark.get("include_unresolved", False)
    compare_resolution = benchmark.get("compare_resolution", False)
    for category, labels in benchmark.get("entities", {}).items():
        expected = {entity_key(category, item, compare_resolution=compare_resolution) for item in labels}
        detected = detected_entities(
            data,
            category,
            scope=scope,
            include_unresolved=include_unresolved,
            compare_resolution=compare_resolution,
        )
        category_scores[category] = category_score(expected, detected)
    for category, labels in benchmark.get("launch_graph", {}).items():
        expected = {record_key(item, LAUNCH_CATEGORY_FIELDS[category]) for item in labels}
        category_scores[category] = category_score(expected, detected_launch_records(data, category))
    diagnostic_labels = benchmark.get("diagnostics", [])
    if diagnostic_labels:
        expected = {record_key(item, DIAGNOSTIC_FIELDS) for item in diagnostic_labels}
        codes = set(benchmark.get("diagnostic_codes", []))
        category_scores["diagnostics"] = category_score(expected, detected_diagnostics(data, codes))
    total_true_positive = sum(item["true_positive"] for item in category_scores.values())
    total_false_positive = sum(item["false_positive"] for item in category_scores.values())
    total_false_negative = sum(item["false_negative"] for item in category_scores.values())
    precision = total_true_positive / (total_true_positive + total_false_positive) if total_true_positive + total_false_positive else 1.0
    recall = total_true_positive / (total_true_positive + total_false_negative) if total_true_positive + total_false_negative else 1.0
    expected_revision = benchmark.get("revision")
    actual_revision = git_revision(path) if expected_revision else None
    revision_matches = expected_revision is None or actual_revision == expected_revision
    return {
        "name": benchmark["name"],
        "path": str(path),
        "labels": total_true_positive + total_false_negative,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "expected_revision": expected_revision,
        "actual_revision": actual_revision,
        "revision_matches": revision_matches,
        "category_scores": category_scores,
        "passed": revision_matches and precision >= benchmark["minimum_precision"] and recall >= benchmark["minimum_recall"],
    }


def parse_overrides(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"path override must be NAME=PATH: {value}")
        name, path = value.split("=", 1)
        result[name] = Path(path).expanduser().resolve()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure entity-level Robot Doctor precision and recall against manual labels.")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("ground_truth") / "manifest.json")
    parser.add_argument("--root", type=Path, default=WORKSPACE)
    parser.add_argument("--path", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--output", type=Path, help="Write detailed benchmark JSON")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    overrides = parse_overrides(args.path)
    results = []
    failed = False
    for benchmark in manifest["benchmarks"]:
        path = overrides.get(benchmark["name"], (args.root / benchmark["path"]).resolve())
        if not path.exists():
            if benchmark.get("required") or args.require_all:
                print(f"FAIL {benchmark['name']}: checkout not found at {path}")
                failed = True
            else:
                print(f"SKIP {benchmark['name']}: checkout not found at {path}")
            continue
        result = score_benchmark(benchmark, path)
        results.append(result)
        label = "PASS" if result["passed"] else "FAIL"
        revision = "" if result["revision_matches"] else f", revision={result['actual_revision'] or '<none>'} expected={result['expected_revision']}"
        print(
            f"{label} {result['name']}: labels={result['labels']}, precision={result['precision']:.3f}, "
            f"recall={result['recall']:.3f}, f1={result['f1']:.3f}{revision}"
        )
        failed = failed or not result["passed"]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"schema_version": manifest["schema_version"], "results": results}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
