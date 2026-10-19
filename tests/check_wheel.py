from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Robot Doctor wheel contents.")
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    with zipfile.ZipFile(args.wheel) as archive:
        names = set(archive.namelist())

    required = {
        "robot_doctor/__init__.py",
        "robot_doctor/__main__.py",
        "robot_doctor/config.py",
        "robot_doctor/intake.py",
        "robot_doctor/scanner.py",
        "robot_doctor/overviews.py",
        "robot_doctor/web.py",
        "robot_doctor/demo.py",
        "robot_doctor/public_case.py",
        "robot_doctor/case_studies/fanuc/case.json",
        "robot_doctor/case_studies/fanuc/initial.json",
        "robot_doctor/case_studies/fanuc/before.json",
        "robot_doctor/case_studies/fanuc/after.json",
        "robot_doctor/case_studies/fanuc/fix.patch",
        "robot_doctor/case_studies/fanuc/licenses/Apache-2.0.txt",
        "robot_doctor/case_studies/moveit/case.json",
        "robot_doctor/case_studies/moveit/before.json",
        "robot_doctor/case_studies/moveit/after.json",
        "robot_doctor/case_studies/moveit/initial.json",
        "robot_doctor/case_studies/moveit/index.html",
        "robot_doctor/case_studies/turtlebot3/case.json",
        "robot_doctor/case_studies/turtlebot3/before.json",
        "robot_doctor/case_studies/turtlebot3/after.json",
        "robot_doctor/case_studies/turtlebot3/initial.json",
        "robot_doctor/case_studies/turtlebot3/index.html",
        "robot_doctor/case_studies/turtlebot3/fix.patch",
        "robot_doctor/case_studies/turtlebot3/licenses/Apache-2.0.txt",
        "robot_doctor/case_studies/moveit/fix.patch",
        "robot_doctor/case_studies/moveit/licenses/panda-description-Apache-2.0.txt",
        "robot_doctor/schemas/robot_doctor_config.schema.json",
        "robot_doctor/schemas/robot_doctor_scan.schema.json",
    }
    missing = sorted(required - names)
    conflicting = sorted(name for name in names if name == "tools/__init__.py" or name.startswith("tools/"))
    if missing:
        raise SystemExit(f"wheel is missing required files: {', '.join(missing)}")
    if conflicting:
        raise SystemExit(f"wheel contains conflicting top-level tools package: {', '.join(conflicting)}")
    print(f"PASS {args.wheel.name}: bundled schema present; no top-level tools package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
