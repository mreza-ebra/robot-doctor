"""Pinned provenance for a public, issue-guided static-analysis case study."""
from pathlib import Path

REPOSITORY_URL = "https://github.com/FANUC-CORPORATION/fanuc_driver"
REVISION = "da5db3f611cdc9dffe687e056c4e79bea584bf5c"
ISSUE_URL = REPOSITORY_URL + "/issues/50"
PACKAGE = "fanuc_moveit_config"
DEPENDENCY = "fanuc_hardware_interface"
MANIFEST = PACKAGE + "/package.xml"
ASSET_NAMES = frozenset({"case.json", "initial.json", "before.json", "after.json", "before.html", "after.html", "fix.patch", "README.md", "THIRD_PARTY_NOTICES.md", "licenses/Apache-2.0.txt", "licenses/BSD-3-Clause.txt", "licenses/BSD-2-Clause.txt", "licenses/MIT.txt"})


def case_directory() -> Path:
    return Path(__file__).with_name("case_studies") / "fanuc"


def is_target(finding: dict) -> bool:
    return finding.get("code") == "RD101" and finding.get("package") == PACKAGE and finding.get("dependency") == DEPENDENCY
