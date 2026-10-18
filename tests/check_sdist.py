from __future__ import annotations

import argparse
import tarfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Robot Doctor source-distribution contents.")
    parser.add_argument("sdist", type=Path)
    args = parser.parse_args()
    required = {
        ".dockerignore",
        "Dockerfile",
        "compose.yaml",
        "start_robot_doctor.command",
        "stop_robot_doctor.command",
        "schemas/robot_doctor_scan.schema.json",
        "src/robot_doctor/web.py",
        "src/robot_doctor/demo.py",
        "tools/build_showcase.py",
        "tools/build_walkthrough.py",
        "tools/run_public_case_study.py",
        "tools/run_moveit_case_study.py",
        "docs/case_studies/moveit/case.json",
        "docs/case_studies/moveit/fix.patch",
        "src/robot_doctor/case_studies/moveit/case.json",
        "docs/case_studies/fanuc/case.json",
        "docs/case_studies/fanuc/fix.patch",
        "src/robot_doctor/case_studies/fanuc/case.json",
        "docs/pilot_kit.md",
        "docs/showcase/index.html",
        "docs/showcase/broken.zip",
        "docs/showcase/fixed.zip",
        "BACKLOG.md",
        "tests/fixtures/cpp_robot/probe_cpp/urdf/control_macro.xacro",
        "tests/fixtures/cpp_robot/probe_cpp/urdf/generated_robot.xacro",
        "tests/fixtures/cpp_robot/probe_cpp/urdf/orphan_macro.xacro",
        "tests/fixtures/cpp_robot/probe_cpp/urdf/unincluded_invocation.xacro",
        "tests/fixtures/cpp_robot/probe_cpp/urdf/unrelated_macro.xacro",
        "tests/run_live_git_intake.py",
    }
    with tarfile.open(args.sdist, "r:gz") as archive:
        names = {"/".join(Path(name).parts[1:]) for name in archive.getnames() if len(Path(name).parts) > 1}
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"source distribution is missing: {', '.join(missing)}")
    print(f"PASS {args.sdist.name}: Docker launchers, schema, application source, and Xacro fixtures present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
