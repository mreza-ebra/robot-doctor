from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).parents[1]
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor.scanner import scan_repository


def parse_overrides(values: list[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"path override must be NAME=PATH: {value}")
        name, path = value.split("=", 1)
        overrides[name] = Path(path).expanduser().resolve()
    return overrides


def git_revision(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def validate_scan(name: str, path: Path, expected: dict[str, Any]) -> list[str]:
    data = scan_repository(path)
    summary = data["summary"]
    failures = []
    for field in ("packages", "launch_files", "nodes", "topics", "services", "actions"):
        if summary[field] != expected[field]:
            failures.append(f"{name}: expected {field}={expected[field]}, found {summary[field]}")
    errors = summary["diagnostics"].get("error", 0)
    if errors > expected.get("max_errors", 0):
        failures.append(f"{name}: expected at most {expected['max_errors']} error diagnostics, found {errors}")
    counts = ", ".join(f"{field}={summary[field]}" for field in ("packages", "launch_files", "nodes", "topics", "services", "actions"))
    print(f"PASS {name}: {counts}, errors={errors}" if not failures else f"FAIL {name}: {counts}, errors={errors}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Robot Doctor against pinned real ROS 2 repositories.")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("real_repositories.json"))
    parser.add_argument("--root", type=Path, default=WORKSPACE, help="Base directory for manifest-relative checkout paths")
    parser.add_argument("--path", action="append", default=[], metavar="NAME=PATH", help="Override a repository or workspace path")
    parser.add_argument("--require-all", action="store_true", help="Fail instead of skipping unavailable checkouts")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    overrides = parse_overrides(args.path)
    failures: list[str] = []
    available_repositories: set[str] = set()

    for repository in manifest["repositories"]:
        name = repository["name"]
        path = overrides.get(name, (args.root / repository["path"]).resolve())
        if not path.exists():
            message = f"{name}: checkout not found at {path}"
            if args.require_all:
                failures.append(message)
                print(f"FAIL {message}")
            else:
                print(f"SKIP {message}")
            continue
        available_repositories.add(name)
        revision = git_revision(path)
        if revision and revision != repository["revision"]:
            failures.append(f"{name}: expected revision {repository['revision']}, found {revision}")
        failures.extend(validate_scan(name, path, repository["expected"]))

    for workspace in manifest.get("workspaces", []):
        name = workspace["name"]
        if not set(workspace["members"]) <= available_repositories and name not in overrides:
            message = f"{name}: not all member repositories are available"
            if args.require_all:
                failures.append(message)
                print(f"FAIL {message}")
            else:
                print(f"SKIP {message}")
            continue
        path = overrides.get(name, (args.root / workspace["path"]).resolve())
        failures.extend(validate_scan(name, path, workspace["expected"]))

    if failures:
        print("\nRegression failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
