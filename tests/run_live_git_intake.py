from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from robot_doctor.intake import clone_git_repository


DEFAULT_REPOSITORY = "https://github.com/mreza-ebra/robot-doctor.git"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live DNS-pinned HTTPS Git intake smoke test.")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="robot-doctor-live-git-") as directory:
        checkout = clone_git_repository(
            args.repository,
            Path(directory) / "repository",
            timeout_seconds=120,
            dns_timeout_seconds=15,
            max_checkout_bytes=100 * 1024 * 1024,
        )
        if not (checkout / "pyproject.toml").is_file():
            raise SystemExit("live Git intake did not produce the expected repository checkout")
    print(f"PASS live DNS-pinned HTTPS intake: {args.repository}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
