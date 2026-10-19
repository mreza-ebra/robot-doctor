"""Pinned provenance for public, issue-guided static-analysis case studies."""
from pathlib import Path

REPOSITORY_URL = "https://github.com/FANUC-CORPORATION/fanuc_driver"
REVISION = "da5db3f611cdc9dffe687e056c4e79bea584bf5c"
ISSUE_URL = REPOSITORY_URL + "/issues/50"
PACKAGE = "fanuc_moveit_config"
DEPENDENCY = "fanuc_hardware_interface"
MANIFEST = PACKAGE + "/package.xml"
ASSET_NAMES = frozenset({"case.json", "initial.json", "before.json", "after.json", "before.html", "after.html", "fix.patch", "README.md", "THIRD_PARTY_NOTICES.md", "licenses/Apache-2.0.txt", "licenses/BSD-3-Clause.txt", "licenses/BSD-2-Clause.txt", "licenses/MIT.txt"})
CASES = {
    "fanuc": {
        "label": "FANUC",
        "title": "FANUC: declare a required runtime dependency",
        "page_title": "A real FANUC driver dependency issue",
        "description": "The launch files need a hardware-interface package that the manifest does not declare. Add one exec_depend entry, then compare the scans.",
        "repository_url": REPOSITORY_URL,
        "revision": REVISION,
        "package": PACKAGE,
        "dependency": DEPENDENCY,
        "showcase_folder": "public_case",
    },
    "moveit": {
        "label": "MoveIt",
        "title": "MoveIt: use the RViz files already in the package",
        "page_title": "A real MoveIt Resources RViz launch issue",
        "description": "The Panda launcher looks for RViz files in moveit2_tutorials. Point it to the package's existing full and empty RViz configurations, then compare the scans.",
        "repository_url": "https://github.com/moveit/moveit_resources",
        "revision": "e1657c128ea61621777a041e3783c9c7c93db4ca",
        "package": "moveit_resources_panda_moveit_config",
        "dependency": "moveit2_tutorials",
        "showcase_folder": "moveit_case",
    },
    "turtlebot3": {
        "label": "TurtleBot3",
        "title": "TurtleBot3: give a standalone launch argument a default",
        "page_title": "A real TurtleBot3 standalone launch issue",
        "description": "The state-publisher launcher reads namespace without a local declaration or default. Declare it and provide an empty default while preserving caller overrides.",
        "repository_url": "https://github.com/ROBOTIS-GIT/turtlebot3",
        "revision": "fc817ce3073af1d6032397c64504134882af5e9a",
        "package": "turtlebot3_bringup",
        "target_code": "RD304",
        "launch_file": "turtlebot3_bringup/launch/turtlebot3_state_publisher.launch.py",
        "launch_configuration": "namespace",
        "showcase_folder": "turtlebot3_case",
    },
}


def case_assets(case_id: str = "fanuc") -> frozenset[str]:
    if case_id == "fanuc":
        return ASSET_NAMES
    if case_id == "moveit":
        return (ASSET_NAMES - {"licenses/Apache-2.0.txt", "licenses/BSD-3-Clause.txt", "licenses/BSD-2-Clause.txt", "licenses/MIT.txt"}) | {"licenses/panda-description-Apache-2.0.txt", "licenses/fanuc-description.txt", "licenses/fanuc-moveit-config.txt"}
    if case_id == "turtlebot3":
        return ASSET_NAMES - {"licenses/BSD-3-Clause.txt", "licenses/BSD-2-Clause.txt", "licenses/MIT.txt"}
    raise ValueError("unknown public case")


def case_directory(case_id: str = "fanuc") -> Path:
    if case_id not in CASES:
        raise ValueError("unknown public case")
    return Path(__file__).with_name("case_studies") / case_id


def is_target(finding: dict, case_id: str = "fanuc") -> bool:
    case = CASES[case_id]
    fields = {"code": case.get("target_code", "RD101"), "package": case["package"]}
    if "dependency" in case:
        fields["dependency"] = case["dependency"]
    else:
        fields.update(launch_file=case["launch_file"], launch_configuration=case["launch_configuration"])
    return all(finding.get(key) == value for key, value in fields.items())
