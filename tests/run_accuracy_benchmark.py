from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).parents[1]
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor.scanner import scan_repository

NAME_ONLY_CATEGORIES = {"executables", "node_names", "declared_parameters"}


def normalized_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().replace("::", "/").replace(".", "/")
    return re.sub(r"/+", "/", normalized).strip("/")


def entity_key(category: str, item: dict[str, Any]) -> tuple[str | None, ...]:
    base = (item.get("package"), item.get("name"))
    return base if category in NAME_ONLY_CATEGORIES else base + (normalized_type(item.get("type")),)


def detected_entities(data: dict[str, Any], category: str) -> set[tuple[str | None, ...]]:
    return {
        entity_key(category, {"package": report["package"]["name"], **item})
        for report in data["packages"]
        for item in report[category]
        if item.get("resolved", True)
    }


def score_benchmark(benchmark: dict[str, Any], path: Path) -> dict[str, Any]:
    data = scan_repository(path)
    category_scores = {}
    total_true_positive = 0
    total_false_positive = 0
    total_false_negative = 0
    for category, labels in benchmark["entities"].items():
        expected = {entity_key(category, item) for item in labels}
        detected = detected_entities(data, category)
        true_positive = len(expected & detected)
        false_positive = len(detected - expected)
        false_negative = len(expected - detected)
        total_true_positive += true_positive
        total_false_positive += false_positive
        total_false_negative += false_negative
        category_scores[category] = {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "unexpected": [list(item) for item in sorted(detected - expected, key=lambda key: tuple("" if value is None else str(value) for value in key))],
            "missing": [list(item) for item in sorted(expected - detected, key=lambda key: tuple("" if value is None else str(value) for value in key))],
        }
    precision = total_true_positive / (total_true_positive + total_false_positive) if total_true_positive + total_false_positive else 1.0
    recall = total_true_positive / (total_true_positive + total_false_negative) if total_true_positive + total_false_negative else 1.0
    return {
        "name": benchmark["name"],
        "path": str(path),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "category_scores": category_scores,
        "passed": precision >= benchmark["minimum_precision"] and recall >= benchmark["minimum_recall"],
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
        print(f"{label} {result['name']}: precision={result['precision']:.3f}, recall={result['recall']:.3f}, f1={result['f1']:.3f}")
        failed = failed or not result["passed"]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"schema_version": manifest["schema_version"], "results": results}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
