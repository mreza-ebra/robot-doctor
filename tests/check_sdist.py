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
    }
    with tarfile.open(args.sdist, "r:gz") as archive:
        names = {"/".join(Path(name).parts[1:]) for name in archive.getnames() if len(Path(name).parts) > 1}
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"source distribution is missing: {', '.join(missing)}")
    print(f"PASS {args.sdist.name}: Docker launchers, schema, and application source present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
