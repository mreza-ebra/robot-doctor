#!/usr/bin/env python3
"""Evidence-backed static analysis for ROS 2 repositories and workspaces."""

from __future__ import annotations

import argparse
import ast
import configparser
import contextvars
import html as html_lib
import json
import os
import platform
import posixpath
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .config import ConfigError, ScanConfig, load_scan_config

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


SCHEMA_VERSION = "1.9.0"
SCANNER_VERSION = "0.6.0.dev0"
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "install",
    "log",
    "node_modules",
}
IGNORE_MARKERS = {"COLCON_IGNORE", "AMENT_IGNORE"}
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".py"}
LAUNCH_SUFFIXES = (".launch.py", ".launch.xml", ".launch.yaml", ".launch.yml", ".launch")
KNOWN_ROS_IMPORTS = {
    "ament_index_python",
    "builtin_interfaces",
    "geometry_msgs",
    "launch",
    "launch_ros",
    "lifecycle_msgs",
    "nav_msgs",
    "rcl_interfaces",
    "rclcpp",
    "rclcpp_action",
    "rclcpp_components",
    "rclcpp_lifecycle",
    "rclpy",
    "rosidl_default_generators",
    "sensor_msgs",
    "std_msgs",
    "std_srvs",
    "tf2",
    "tf2_geometry_msgs",
    "tf2_ros",
    "trajectory_msgs",
    "visualization_msgs",
}

ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class ScanCancelled(RuntimeError):
    pass


@dataclass
class ScanSession:
    root: Path
    config: ScanConfig
    progress: ProgressCallback | None = None
    cancel_check: CancelCheck | None = None
    input_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    cached_text: dict[Path, str] = field(default_factory=dict)
    skipped_files: set[Path] = field(default_factory=set)
    recorded_issues: set[tuple[str, str]] = field(default_factory=set)
    files_read: int = 0
    bytes_read: int = 0
    repository_entries_seen: int = 0

    def check_cancelled(self) -> None:
        if self.cancel_check and self.cancel_check():
            raise ScanCancelled("scan cancelled")

    def emit(self, stage: str, current: int, total: int, path: Path | None = None, message: str = "") -> None:
        self.check_cancelled()
        if self.progress:
            self.progress(
                {
                    "stage": stage,
                    "current": current,
                    "total": total,
                    "path": relative(path, self.root) if path else None,
                    "message": message,
                }
            )

    def record_input_issue(self, code: str, severity: str, title: str, message: str, path: Path, snippet: str) -> None:
        key = (code, str(path))
        if key in self.recorded_issues:
            return
        self.recorded_issues.add(key)
        self.input_diagnostics.append(
            diagnostic(
                code,
                severity,
                title,
                message,
                [evidence(relative(path, self.root), None, "safe_reader", snippet)],
                1.0,
                file=relative(path, self.root),
            )
        )


_ACTIVE_SCAN_SESSION: contextvars.ContextVar[ScanSession | None] = contextvars.ContextVar("robot_doctor_scan_session", default=None)


def relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_text(path: Path) -> str:
    session = _ACTIVE_SCAN_SESSION.get()
    resolved = path.resolve()
    if session is None:
        return path.read_text(encoding="utf-8", errors="replace")
    session.check_cancelled()
    if resolved in session.cached_text:
        return session.cached_text[resolved]
    if resolved in session.skipped_files:
        return ""
    if session.files_read >= session.config.max_files:
        session.skipped_files.add(resolved)
        session.record_input_issue(
            "RD007",
            "warning",
            "File-count limit reached",
            f"Scanning stopped reading new files after {session.config.max_files} files.",
            session.root,
            str(session.config.max_files),
        )
        return ""
    try:
        size = path.stat().st_size
    except OSError as exc:
        session.skipped_files.add(resolved)
        session.record_input_issue(
            "RD004",
            "warning",
            "Unreadable source file",
            f"{relative(path, session.root)} could not be inspected and was skipped: {exc}.",
            path,
            str(exc),
        )
        return ""
    if size > session.config.max_file_size_bytes:
        session.skipped_files.add(resolved)
        session.record_input_issue(
            "RD005",
            "warning",
            "Oversized source file",
            f"{relative(path, session.root)} is {size} bytes and exceeds the {session.config.max_file_size_bytes}-byte limit.",
            path,
            str(size),
        )
        return ""
    if session.bytes_read + size > session.config.max_total_size_bytes:
        session.skipped_files.add(resolved)
        session.record_input_issue(
            "RD009",
            "warning",
            "Total input-size limit reached",
            f"Reading {relative(path, session.root)} would exceed the {session.config.max_total_size_bytes}-byte total input limit.",
            session.root,
            str(session.config.max_total_size_bytes),
        )
        return ""
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        session.skipped_files.add(resolved)
        session.record_input_issue(
            "RD004",
            "warning",
            "Unreadable source file",
            f"{relative(path, session.root)} could not be read and was skipped: {exc}.",
            path,
            str(exc),
        )
        return ""
    session.files_read += 1
    session.bytes_read += size
    session.cached_text[resolved] = value
    return value


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def compact_snippet(value: str, limit: int = 180) -> str:
    result = " ".join(value.strip().split())
    return result if len(result) <= limit else result[: limit - 1] + "…"


def evidence(file: str, line: int | None, extractor: str, snippet: str = "") -> dict[str, Any]:
    return {
        "file": file,
        "line": line,
        "extractor": extractor,
        "snippet": compact_snippet(snippet),
    }


def finding(
    kind: str,
    name: str | None,
    type_name: str | None,
    file: str,
    line: int | None,
    extractor: str,
    snippet: str,
    *,
    confidence: float = 0.95,
    fact_type: str = "detected",
    resolved: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    item = {
        "kind": kind,
        "name": name,
        "type": type_name,
        "file": file,
        "line": line,
        "fact_type": fact_type,
        "confidence": round(confidence, 2),
        "resolved": resolved,
        "evidence": [evidence(file, line, extractor, snippet)],
    }
    item.update(extra)
    return item


def unique_findings(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in items:
        key = json.dumps(
            {
                "kind": item.get("kind"),
                "name": item.get("name"),
                "type": item.get("type"),
                "file": item.get("file"),
                "line": item.get("line"),
                "default": item.get("default"),
                "value": item.get("value"),
            },
            sort_keys=True,
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return sorted(result, key=lambda item: (item.get("file") or "", item.get("line") or 0, item.get("kind") or ""))


def iter_repository_files(root: Path, *, max_entries: int | None = None) -> Iterator[Path]:
    """Walk source files while honoring colcon artifacts and ignore markers."""
    if any((root / marker).exists() for marker in IGNORE_MARKERS):
        return
    session = _ACTIVE_SCAN_SESSION.get()
    entry_limit = max_entries
    if session:
        entry_limit = (
            min(entry_limit, session.config.max_repository_entries)
            if entry_limit is not None
            else session.config.max_repository_entries
        )
    local_entries_seen = 0
    directories = [root]
    while directories:
        current_path = directories.pop()
        if session:
            session.check_cancelled()
        if current_path != root and any((current_path / marker).exists() for marker in IGNORE_MARKERS):
            continue
        try:
            entries = os.scandir(current_path)
        except OSError as exc:
            if session:
                session.record_input_issue(
                    "RD004",
                    "warning",
                    "Unreadable directory",
                    f"{relative(current_path, root)} could not be traversed and was skipped: {exc}.",
                    current_path,
                    str(exc),
                )
            continue
        with entries:
            for entry in entries:
                if session:
                    session.check_cancelled()
                    session.repository_entries_seen += 1
                    entries_seen = session.repository_entries_seen
                else:
                    local_entries_seen += 1
                    entries_seen = local_entries_seen
                if entry_limit is not None and entries_seen > entry_limit:
                    if session:
                        session.record_input_issue(
                            "RD010",
                            "warning",
                            "Repository-entry limit reached",
                            f"Repository traversal stopped after {entry_limit} files and directories.",
                            root,
                            str(entry_limit),
                        )
                    return
                path = Path(entry.path)
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in IGNORED_DIRECTORY_NAMES and not any((path / marker).exists() for marker in IGNORE_MARKERS):
                            directories.append(path)
                    elif entry.name not in IGNORE_MARKERS:
                        yield path
                except OSError as exc:
                    if session:
                        session.record_input_issue(
                            "RD004",
                            "warning",
                            "Unreadable repository entry",
                            f"{relative(path, root)} could not be inspected and was skipped: {exc}.",
                            path,
                            str(exc),
                        )


def is_scan_candidate(path: Path) -> bool:
    return (
        path.name in {"package.xml", "CMakeLists.txt", "setup.py", "setup.cfg", "pyproject.toml"}
        or any(path.name.endswith(suffix) for suffix in LAUNCH_SUFFIXES)
        or path.suffix in SOURCE_EXTENSIONS | {".msg", ".srv", ".action", ".yaml", ".yml", ".xml", ".urdf", ".xacro"}
    )


def parse_package_xml(path: Path, root: Path) -> dict[str, Any] | None:
    content = read_text(path)
    session = _ACTIVE_SCAN_SESSION.get()
    if session and path.resolve() in session.skipped_files:
        return None
    package_node = ET.fromstring(content)

    def text(tag: str) -> str | None:
        node = package_node.find(tag)
        return node.text.strip() if node is not None and node.text else None

    dependencies: dict[str, list[str]] = defaultdict(list)
    for child in package_node:
        if child.tag == "depend" or child.tag.endswith("_depend"):
            if child.text:
                dependencies[child.tag].append(child.text.strip())
    build_type = None
    export = package_node.find("export")
    if export is not None:
        build_node = export.find("build_type")
        if build_node is not None and build_node.text:
            build_type = build_node.text.strip()
    rel_file = relative(path, root)
    return {
        "name": text("name") or path.parent.name,
        "path": relative(path.parent, root),
        "version": text("version"),
        "description": text("description"),
        "build_type": build_type,
        "dependencies": {key: sorted(set(values)) for key, values in sorted(dependencies.items())},
        "fact_type": "detected",
        "confidence": 1.0,
        "evidence": [evidence(rel_file, 1, "package_xml", "<package>")],
    }


def collect_scan_files(root: Path) -> list[Path]:
    session = _ACTIVE_SCAN_SESSION.get()
    if session is None:
        return sorted(path for path in iter_repository_files(root) if is_scan_candidate(path))
    scan_files = []
    session.emit("enumerate_repository", 0, session.config.max_repository_entries, message="Enumerating bounded repository input")
    for path in iter_repository_files(root):
        if is_scan_candidate(path):
            if len(scan_files) >= session.config.max_files:
                session.record_input_issue(
                    "RD007",
                    "warning",
                    "File-count limit reached",
                    f"Repository discovery stopped after {session.config.max_files} scan candidate files.",
                    root,
                    str(session.config.max_files),
                )
                break
            scan_files.append(path)
        if session.repository_entries_seen % 500 == 0:
            session.emit(
                "enumerate_repository",
                session.repository_entries_seen,
                session.config.max_repository_entries,
                path,
                "Enumerating bounded repository input",
            )
    session.emit(
        "enumerate_repository",
        session.repository_entries_seen,
        session.config.max_repository_entries,
        message=f"Selected {len(scan_files)} scan candidate file(s)",
    )
    return sorted(scan_files)


def discover_packages(root: Path, scan_files: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packages: list[dict[str, Any]] = []
    parse_issues: list[dict[str, Any]] = []
    for path in (file for file in scan_files if file.name == "package.xml"):
        try:
            package = parse_package_xml(path, root)
            if package:
                packages.append(package)
        except ET.ParseError as exc:
            parse_issues.append(
                diagnostic(
                    "RD002",
                    "error",
                    "Invalid package.xml",
                    f"{relative(path, root)} cannot be parsed: {exc}.",
                    [evidence(relative(path, root), getattr(exc, "position", (None,))[0], "xml_parser", str(exc))],
                    1.0,
                )
            )
    return sorted(packages, key=lambda item: (item["path"], item["name"])), parse_issues


def package_for_path(path: Path, packages: list[dict[str, Any]], root: Path) -> dict[str, Any] | None:
    rel = relative(path, root)
    candidates = [item for item in packages if rel == item["path"] or rel.startswith(item["path"] + "/")]
    return max(candidates, key=lambda item: len(item["path"])) if candidates else None


def expression_text(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def canonical_ros_type(value: str | None) -> str | None:
    if not value:
        return value
    normalized = value.strip().replace("::", "/")
    return re.sub(r"\.(msg|srv|action)\.", r"/\1/", normalized)


def cpp_type_aliases(text: str) -> dict[str, str]:
    return {
        match.group(1): canonical_ros_type(match.group(2).strip()) or match.group(2).strip()
        for match in re.finditer(r"\busing\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+);", text)
    }


def resolve_cpp_type(value: str | None, aliases: dict[str, str]) -> str | None:
    canonical = canonical_ros_type(value)
    resolved = aliases.get(canonical or "", canonical)
    adapter = re.fullmatch(r"rclcpp/TypeAdapter<(.+)>", resolved or "")
    if adapter:
        arguments = split_cpp_template_arguments(adapter.group(1))
        return canonical_ros_type(arguments[-1]) if arguments else resolved
    return resolved


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return call_name(node.func)
    return ""


def evaluate_python_expression(node: ast.AST | None, constants: dict[str, Any]) -> tuple[Any, float, bool]:
    if node is None:
        return None, 0.0, True
    if isinstance(node, ast.Constant):
        return node.value, 1.0, False
    if isinstance(node, ast.Name):
        if node.id in constants:
            return constants[node.id], 0.92, False
        return node.id, 0.45, True
    if isinstance(node, ast.Attribute):
        return call_name(node), 0.55, True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [evaluate_python_expression(item, constants) for item in node.elts]
        return [item[0] for item in values], min((item[1] for item in values), default=1.0), any(item[2] for item in values)
    if isinstance(node, ast.Dict):
        result = {}
        confidences = []
        dynamic = False
        for key_node, value_node in zip(node.keys, node.values):
            key, key_confidence, key_dynamic = evaluate_python_expression(key_node, constants)
            value, value_confidence, value_dynamic = evaluate_python_expression(value_node, constants)
            result[str(key)] = value
            confidences.extend([key_confidence, value_confidence])
            dynamic = dynamic or key_dynamic or value_dynamic
        return result, min(confidences, default=0.9), dynamic
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + (expression_text(value.value) or "dynamic") + "}")
        return "".join(parts), 0.5, True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        left, left_confidence, left_dynamic = evaluate_python_expression(node.left, constants)
        right, right_confidence, right_dynamic = evaluate_python_expression(node.right, constants)
        separator = "/" if isinstance(node.op, ast.Div) else ""
        return f"{left}{separator}{right}", min(left_confidence, right_confidence, 0.85), left_dynamic or right_dynamic
    if isinstance(node, ast.Call):
        short = call_name(node.func).split(".")[-1]
        if short == "LaunchConfiguration" and node.args:
            value, _, _ = evaluate_python_expression(node.args[0], constants)
            return f"$(var {value})", 0.75, True
        if short in {"FindPackageShare", "get_package_share_directory"} and node.args:
            value, _, dynamic = evaluate_python_expression(node.args[0], constants)
            return f"$(find-pkg-share {value})", 0.8, dynamic
        if (short == "PathJoinSubstitution" or short.endswith("LaunchDescriptionSource")) and node.args:
            value, confidence, dynamic = evaluate_python_expression(node.args[0], constants)
            if isinstance(value, list):
                return "/".join(str(item).strip("/") for item in value), confidence, dynamic
            return value, confidence, dynamic
        if short == "TextSubstitution":
            keyword = next((item.value for item in node.keywords if item.arg == "text"), None)
            return evaluate_python_expression(keyword, constants)
    return expression_text(node), 0.4, True


def source_segment(text: str, node: ast.AST) -> str:
    return ast.get_source_segment(text, node) or expression_text(node) or ""


def qos_from_python(node: ast.AST | None, constants: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in profiles:
        return dict(profiles[node.id])
    raw = expression_text(node) or ""
    result: dict[str, Any] = {"expression": raw}
    value, _, dynamic = evaluate_python_expression(node, constants)
    if isinstance(value, int):
        result["depth"] = value
    lowered = raw.lower()
    if "best_effort" in lowered:
        result["reliability"] = "best_effort"
    elif "reliable" in lowered:
        result["reliability"] = "reliable"
    if "transient_local" in lowered:
        result["durability"] = "transient_local"
    elif "volatile" in lowered:
        result["durability"] = "volatile"
    if dynamic:
        result["dynamic"] = True
    return result


class PythonSourceVisitor(ast.NodeVisitor):
    def __init__(self, text: str, file: str) -> None:
        self.text = text
        self.file = file
        self.constants: dict[str, Any] = {}
        self.qos_profiles: dict[str, dict[str, Any]] = {}
        self.imports: set[str] = set()
        self.aliases: dict[str, str] = {}
        self.class_stack: list[tuple[str, bool, bool]] = []
        self.items: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            root = name.name.split(".")[0]
            self.imports.add(root)
            self.aliases[name.asname or root] = name.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module:
            self.imports.add(module.split(".")[0])
        for name in node.names:
            self.aliases[name.asname or name.name] = f"{module}.{name.name}".strip(".")

    def visit_Assign(self, node: ast.Assign) -> None:
        value, _, dynamic = evaluate_python_expression(node.value, self.constants)
        for target in node.targets:
            if isinstance(target, ast.Name) and not dynamic and isinstance(value, (str, int, float, bool)):
                self.constants[target.id] = value
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Call) and call_name(node.value.func).endswith("QoSProfile"):
                self.qos_profiles[target.id] = qos_from_python(node.value, self.constants, {}) or {}
                for keyword in node.value.keywords:
                    raw = (expression_text(keyword.value) or "").lower()
                    if keyword.arg == "reliability":
                        self.qos_profiles[target.id]["reliability"] = "best_effort" if "best_effort" in raw else "reliable" if "reliable" in raw else raw
                    if keyword.arg == "durability":
                        self.qos_profiles[target.id]["durability"] = "transient_local" if "transient_local" in raw else "volatile" if "volatile" in raw else raw
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = " ".join(expression_text(base) or "" for base in node.bases)
        lifecycle = "LifecycleNode" in bases
        ros_node = bool(re.search(r"(?<![A-Za-z0-9_])(?:LifecycleNode|Node)(?![A-Za-z0-9_])", bases))
        self.class_stack.append((node.name, lifecycle, ros_node))
        self.generic_visit(node)
        self.class_stack.pop()

    def add_call_finding(
        self,
        collection: str,
        kind: str,
        node: ast.Call,
        name_index: int,
        type_index: int | None,
        qos_index: int | None = None,
        default_index: int | None = None,
        name_keywords: tuple[str, ...] = (),
        type_keywords: tuple[str, ...] = (),
        qos_keywords: tuple[str, ...] = (),
        default_keywords: tuple[str, ...] = (),
    ) -> None:
        def argument(index: int | None, keywords: tuple[str, ...]) -> ast.AST | None:
            if index is not None and len(node.args) > index:
                return node.args[index]
            return next((item.value for item in node.keywords if item.arg in keywords), None)

        name_node = argument(name_index, name_keywords)
        value, confidence, dynamic = evaluate_python_expression(name_node, self.constants)
        type_node = argument(type_index, type_keywords)
        type_name = expression_text(type_node)
        if isinstance(type_node, ast.Name) and type_node.id in self.aliases:
            type_name = self.aliases[type_node.id]
        type_name = canonical_ros_type(type_name)
        extras: dict[str, Any] = {}
        qos_node = argument(qos_index, qos_keywords)
        if qos_node is not None:
            extras["qos"] = qos_from_python(qos_node, self.constants, self.qos_profiles)
        default_node = argument(default_index, default_keywords)
        if default_node is not None:
            default, _, default_dynamic = evaluate_python_expression(default_node, self.constants)
            extras["default"] = default
            extras["default_resolved"] = not default_dynamic
        if self.class_stack:
            extras["class"] = self.class_stack[-1][0]
            extras["lifecycle"] = self.class_stack[-1][1]
        self.items[collection].append(
            finding(
                kind,
                str(value) if value is not None else None,
                type_name,
                self.file,
                node.lineno,
                "python_ast",
                source_segment(self.text, node),
                confidence=confidence,
                resolved=not dynamic,
                **extras,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        full_name = call_name(node.func)
        short = full_name.split(".")[-1]
        resolved_name = self.aliases.get(short, full_name)
        if short == "create_publisher":
            self.add_call_finding("publishers", "publisher", node, 1, 0, 2, name_keywords=("topic",), type_keywords=("msg_type",), qos_keywords=("qos_profile",))
        elif short == "create_subscription":
            self.add_call_finding("subscriptions", "subscription", node, 1, 0, 3, name_keywords=("topic",), type_keywords=("msg_type",), qos_keywords=("qos_profile",))
        elif short == "create_service":
            self.add_call_finding("service_servers", "service_server", node, 1, 0, 3, name_keywords=("srv_name",), type_keywords=("srv_type",), qos_keywords=("qos_profile",))
        elif short == "create_client" and "action" not in resolved_name.lower():
            self.add_call_finding("service_clients", "service_client", node, 1, 0, 2, name_keywords=("srv_name",), type_keywords=("srv_type",), qos_keywords=("qos_profile",))
        elif short == "declare_parameter":
            self.add_call_finding("declared_parameters", "parameter", node, 0, None, default_index=1, name_keywords=("name",), default_keywords=("value",))
        elif short in {"ActionServer", "Server"} and "action" in resolved_name.lower():
            self.add_call_finding("action_servers", "action_server", node, 2, 1, name_keywords=("action_name",), type_keywords=("action_type",))
        elif short in {"ActionClient", "Client"} and "action" in resolved_name.lower():
            self.add_call_finding("action_clients", "action_client", node, 2, 1, name_keywords=("action_name",), type_keywords=("action_type",))
        elif short == "create_node":
            self.add_call_finding("node_names", "node_name", node, 0, None, name_keywords=("node_name", "name"))
        elif short == "__init__":
            owner = call_name(node.func.value) if isinstance(node.func, ast.Attribute) else ""
            explicit_node_init = owner in {"Node", "LifecycleNode", "rclpy.node.Node", "rclpy.lifecycle.Node"}
            ros_super_init = owner == "super" and bool(self.class_stack and self.class_stack[-1][2])
            if explicit_node_init or ros_super_init:
                self.add_call_finding("node_names", "node_name", node, 0, None, name_keywords=("node_name", "name"))
        self.generic_visit(node)


def scan_python_source(path: Path, root: Path) -> tuple[dict[str, list[dict[str, Any]]], set[str], list[dict[str, Any]]]:
    text = read_text(path)
    rel_file = relative(path, root)
    try:
        tree = ast.parse(text, filename=rel_file)
    except SyntaxError as exc:
        return {}, set(), [
            diagnostic(
                "RD003",
                "warning",
                "Python source could not be parsed",
                f"Static extraction skipped {rel_file}: {exc.msg}.",
                [evidence(rel_file, exc.lineno, "python_ast", exc.text or exc.msg)],
                1.0,
            )
        ]
    visitor = PythonSourceVisitor(text, rel_file)
    visitor.visit(tree)
    return {key: unique_findings(value) for key, value in visitor.items.items()}, visitor.imports, []


def extract_call_block(text: str, start: int) -> str:
    open_paren = text.find("(", start)
    if open_paren == -1:
        return ""
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_paren, len(text)):
        character = text[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def split_arguments(block: str) -> list[str]:
    start = block.find("(")
    end = block.rfind(")")
    content = block[start + 1 : end if end > start else len(block)]
    arguments: list[str] = []
    current: list[str] = []
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
    quote: str | None = None
    escaped = False
    for character in content:
        if quote:
            current.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
        elif character in depths:
            depths[character] += 1
            current.append(character)
        elif character in pairs:
            depths[pairs[character]] = max(0, depths[pairs[character]] - 1)
            current.append(character)
        elif character == "," and not any(depths.values()):
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current or content.strip():
        arguments.append("".join(current).strip())
    return arguments


def cpp_value(expression: str | None) -> tuple[str | None, float, bool]:
    if expression is None:
        return None, 0.0, True
    expression = expression.strip()
    match = re.fullmatch(r'(?:R"\([^)]*\)"|[uULR]*"((?:\\.|[^"\\])*)"|[uUL]*\'((?:\\.|[^\'\\])*)\')', expression)
    if match:
        value = next((group for group in match.groups() if group is not None), expression)
        return value, 1.0, False
    return compact_snippet(expression), 0.48, True


def qos_from_cpp(expression: str | None) -> dict[str, Any] | None:
    if not expression:
        return None
    lowered = expression.lower()
    result: dict[str, Any] = {"expression": compact_snippet(expression)}
    depth = re.search(r"(?:qos|keep_last)\s*\(\s*(\d+)\s*\)", lowered)
    if depth:
        result["depth"] = int(depth.group(1))
    if "best_effort" in lowered:
        result["reliability"] = "best_effort"
    elif "reliable" in lowered:
        result["reliability"] = "reliable"
    if "transient_local" in lowered:
        result["durability"] = "transient_local"
    elif "durability_volatile" in lowered or ".volatile" in lowered:
        result["durability"] = "volatile"
    return result


def scan_cpp_source(path: Path, root: Path) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    text = read_text(path)
    rel_file = relative(path, root)
    items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    class_ranges = parse_cpp_classes(path, root)
    type_aliases = cpp_type_aliases(text)

    def enclosing_class(offset: int) -> str | None:
        match = next((item for item in class_ranges if item["body_offset"] <= offset < item["end"]), None)
        return match["name"] if match else None

    referenced_packages = {match.group(1) for match in re.finditer(r"^\s*#\s*include\s*[<\"]([A-Za-z][A-Za-z0-9_]*)/", text, re.MULTILINE)}
    patterns = [
        ("publishers", "publisher", "create_publisher", 0, 1),
        ("subscriptions", "subscription", "create_subscription", 0, 1),
        ("service_servers", "service_server", "create_service", 0, None),
        ("service_clients", "service_client", "create_client", 0, None),
        ("declared_parameters", "parameter", "declare_parameter", 0, None),
    ]
    action_spans: list[tuple[int, int]] = []
    for match in re.finditer(r"rclcpp_action::create_(server|client)\s*<\s*([^;()]+?)\s*>\s*\(", text):
        block = extract_call_block(text, match.start())
        arguments = split_arguments(block)
        name_expression = arguments[1] if len(arguments) > 1 else None
        name, confidence, dynamic = cpp_value(name_expression)
        collection = "action_servers" if match.group(1) == "server" else "action_clients"
        items[collection].append(
            finding(
                "action_server" if match.group(1) == "server" else "action_client",
                name,
                resolve_cpp_type(compact_snippet(match.group(2)), type_aliases),
                rel_file,
                line_number(text, match.start()),
                "cpp_call_parser",
                block,
                confidence=confidence,
                resolved=not dynamic,
                class_name=enclosing_class(match.start()),
            )
        )
        action_spans.append((match.start(), match.start() + len(block)))
    for collection, kind, call, name_index, qos_index in patterns:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){call}\s*(?:<\s*([^;()]+?)\s*>)?\s*\(")
        for match in pattern.finditer(text):
            if call == "create_client" and any(start <= match.start() <= end for start, end in action_spans):
                continue
            if call != "declare_parameter" and not match.group(1):
                continue
            block = extract_call_block(text, match.start())
            arguments = split_arguments(block)
            name_expression = arguments[name_index] if len(arguments) > name_index else None
            name, confidence, dynamic = cpp_value(name_expression)
            extras: dict[str, Any] = {}
            if qos_index is not None and len(arguments) > qos_index:
                extras["qos"] = qos_from_cpp(arguments[qos_index])
            if call == "declare_parameter" and len(arguments) > 1:
                default, _, default_dynamic = cpp_value(arguments[1])
                extras.update(default=default, default_resolved=not default_dynamic)
            items[collection].append(
                finding(
                    kind,
                    name,
                    resolve_cpp_type(compact_snippet(match.group(1)), type_aliases) if match.group(1) else None,
                    rel_file,
                    line_number(text, match.start()),
                    "cpp_call_parser",
                    block,
                    confidence=confidence,
                    resolved=not dynamic,
                    lifecycle="LifecycleNode" in text or "rclcpp_lifecycle" in text,
                    class_name=enclosing_class(match.start()),
                    **extras,
                )
            )
    node_pattern = re.compile(r"(?:(?:rclcpp|rclcpp_lifecycle)::)?(?:Lifecycle)?Node\s*\(\s*([^,\)]+)")
    for match in node_pattern.finditer(text):
        name, confidence, dynamic = cpp_value(match.group(1))
        if dynamic:
            continue
        items["node_names"].append(
            finding(
                "node_name",
                name,
                "rclcpp_lifecycle" if "LifecycleNode" in match.group(0) else "rclcpp",
                rel_file,
                line_number(text, match.start()),
                "cpp_call_parser",
                match.group(0),
                confidence=confidence,
                resolved=True,
                lifecycle="LifecycleNode" in match.group(0),
                class_name=enclosing_class(match.start()),
            )
        )
    if "TransformBroadcaster" in text or "StaticTransformBroadcaster" in text:
        for match in re.finditer(r"(?:Static)?TransformBroadcaster", text):
            items["tf_broadcasters"].append(
                finding("tf_broadcaster", None, match.group(0), rel_file, line_number(text, match.start()), "cpp_token", match.group(0), confidence=0.85, resolved=False, class_name=enclosing_class(match.start()))
            )
    return {key: unique_findings(value) for key, value in items.items()}, referenced_packages


def extract_braced_block(text: str, open_brace: int) -> tuple[str, int]:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_brace, len(text)):
        character = text[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : index + 1], index + 1
    return text[open_brace:], len(text)


def cpp_parameter_name(parameter: str) -> str | None:
    declaration = parameter.split("=", 1)[0].strip()
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", declaration)
    return identifiers[-1] if identifiers else None


def split_cpp_template_arguments(value: str) -> list[str]:
    arguments = []
    current = []
    depth = 0
    for character in value:
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        if character == "," and depth == 0:
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current:
        arguments.append("".join(current).strip())
    return arguments


def parse_cpp_classes(path: Path, root: Path) -> list[dict[str, Any]]:
    text = read_text(path)
    rel_file = relative(path, root)
    class_pattern = re.compile(
        r"(?:template\s*<\s*([^>]+)\s*>\s*)?(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*public\s+([^\{]+))?\s*\{",
        re.DOTALL,
    )
    classes = []
    for match in class_pattern.finditer(text):
        open_brace = text.find("{", match.start())
        body, end = extract_braced_block(text, open_brace)
        template_parameters = re.findall(r"(?:typename|class)\s+([A-Za-z_][A-Za-z0-9_]*)", match.group(1) or "")
        constructors = []
        constructor_pattern = re.compile(rf"(?:explicit\s+)?\b{re.escape(match.group(2))}\s*\(")
        for constructor_match in constructor_pattern.finditer(body):
            signature = extract_call_block(body, constructor_match.start())
            parameters = [cpp_parameter_name(item) for item in split_arguments(signature)]
            signature_end = constructor_match.start() + len(signature)
            body_start = body.find("{", signature_end)
            declaration_tail = body[signature_end:body_start] if body_start != -1 else ""
            constructors.append(
                {
                    "parameters": parameters,
                    "initializer": declaration_tail,
                    "line": line_number(text, open_brace + constructor_match.start()),
                }
            )
        classes.append(
            {
                "name": match.group(2),
                "template_parameters": template_parameters,
                "bases": match.group(3) or "",
                "body": body,
                "body_offset": open_brace,
                "file": rel_file,
                "text": text,
                "constructors": constructors,
                "end": end,
            }
        )
    return classes


def discover_cpp_wrapper_models(paths: list[Path], root: Path) -> tuple[dict[str, dict[str, Any]], set[tuple[str, int]]]:
    classes = [item for path in paths for item in parse_cpp_classes(path, root)]
    models: dict[str, dict[str, Any]] = {}
    factory_locations: set[tuple[str, int]] = set()
    for class_info in classes:
        body = class_info["body"]
        factory_calls: list[tuple[str, str, int, str, int]] = []
        action_spans = []
        for match in re.finditer(r"rclcpp_action::create_(server|client)\s*<\s*([^;()]+?)\s*>\s*\(", body):
            block = extract_call_block(body, match.start())
            arguments = split_arguments(block)
            if len(arguments) > 1:
                collection = "action_servers" if match.group(1) == "server" else "action_clients"
                kind = "action_server" if match.group(1) == "server" else "action_client"
                factory_calls.append((collection, kind, 1, match.group(2).strip(), match.start()))
            action_spans.append((match.start(), match.start() + len(block)))
        standard_factories = [
            ("publishers", "publisher", "create_publisher", 0),
            ("subscriptions", "subscription", "create_subscription", 0),
            ("service_servers", "service_server", "create_service", 0),
            ("service_clients", "service_client", "create_client", 0),
        ]
        for collection, kind, call, name_index in standard_factories:
            for match in re.finditer(rf"(?<![A-Za-z0-9_]){call}\s*<\s*([^;()]+?)\s*>\s*\(", body):
                if call == "create_client" and any(start <= match.start() <= end for start, end in action_spans):
                    continue
                block = extract_call_block(body, match.start())
                arguments = split_arguments(block)
                if len(arguments) > name_index:
                    factory_calls.append((collection, kind, name_index, match.group(1).strip(), match.start()))
        for collection, kind, factory_name_index, factory_type, factory_offset in factory_calls:
            block = extract_call_block(body, factory_offset)
            factory_arguments = split_arguments(block)
            factory_name = factory_arguments[factory_name_index].strip()
            constructor = next(
                (
                    item
                    for item in class_info["constructors"]
                    if factory_name in item["parameters"]
                ),
                None,
            )
            if not constructor:
                continue
            type_parameter_index = next(
                (index for index, parameter in enumerate(class_info["template_parameters"]) if parameter == factory_type),
                None,
            )
            if type_parameter_index is None:
                continue
            absolute_line = line_number(class_info["text"], class_info["body_offset"] + factory_offset)
            models[class_info["name"]] = {
                "class": class_info["name"],
                "collection": collection,
                "kind": kind,
                "name_index": constructor["parameters"].index(factory_name),
                "type_parameter_index": type_parameter_index,
                "file": class_info["file"],
                "line": absolute_line,
                "evidence": [evidence(class_info["file"], absolute_line, "cpp_wrapper_factory", block)],
            }
            factory_locations.add((class_info["file"], absolute_line))
    changed = True
    while changed:
        changed = False
        for class_info in classes:
            if class_info["name"] in models:
                continue
            base_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*<\s*([^>]+)\s*>", class_info["bases"])
            if not base_match or base_match.group(1) not in models:
                continue
            base_model = models[base_match.group(1)]
            base_types = split_cpp_template_arguments(base_match.group(2))
            if len(base_types) <= base_model["type_parameter_index"]:
                continue
            derived_type = base_types[base_model["type_parameter_index"]].strip()
            derived_type_index = next((index for index, parameter in enumerate(class_info["template_parameters"]) if parameter == derived_type), None)
            if derived_type_index is None:
                continue
            for constructor in class_info["constructors"]:
                initializer_match = re.search(rf"\b{re.escape(base_match.group(1))}\s*<[^>]+>\s*\(", constructor["initializer"])
                if not initializer_match:
                    continue
                initializer_call = extract_call_block(constructor["initializer"], initializer_match.start())
                initializer_arguments = split_arguments(initializer_call)
                if len(initializer_arguments) <= base_model["name_index"]:
                    continue
                name_parameter = initializer_arguments[base_model["name_index"]].strip()
                if name_parameter not in constructor["parameters"]:
                    continue
                models[class_info["name"]] = {
                    **base_model,
                    "class": class_info["name"],
                    "name_index": constructor["parameters"].index(name_parameter),
                    "type_parameter_index": derived_type_index,
                    "evidence": base_model["evidence"] + [evidence(class_info["file"], constructor["line"], "cpp_wrapper_inheritance", constructor["initializer"])],
                }
                changed = True
                break
    return models, factory_locations


def scan_cpp_wrapper_instantiations(path: Path, root: Path, models: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not models:
        return {}
    text = read_text(path)
    rel_file = relative(path, root)
    class_ranges = parse_cpp_classes(path, root)
    type_aliases = cpp_type_aliases(text)

    def enclosing_class(offset: int) -> str | None:
        match = next((item for item in class_ranges if item["body_offset"] <= offset < item["end"]), None)
        return match["name"] if match else None

    results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pattern = re.compile(r"(?:std::)?make_(?:unique|shared)\s*<\s*([A-Za-z_][A-Za-z0-9_:]*)\s*<\s*([^<>]+)\s*>\s*>\s*\(")
    for match in pattern.finditer(text):
        class_name = match.group(1).split("::")[-1]
        model = models.get(class_name)
        if not model:
            continue
        block = extract_call_block(text, match.start())
        arguments = split_arguments(block)
        type_arguments = split_cpp_template_arguments(match.group(2))
        if len(arguments) <= model["name_index"] or len(type_arguments) <= model["type_parameter_index"]:
            continue
        name, confidence, dynamic = cpp_value(arguments[model["name_index"]])
        line = line_number(text, match.start())
        item = finding(
            model["kind"],
            name,
            resolve_cpp_type(type_arguments[model["type_parameter_index"]], type_aliases),
            rel_file,
            line,
            "cpp_wrapper_resolution",
            block,
            confidence=min(confidence, 0.94),
            resolved=not dynamic,
            wrapper={"class": class_name, "factory_evidence": model["evidence"]},
            class_name=enclosing_class(match.start()),
        )
        results[model["collection"]].append(item)
    return {key: unique_findings(value) for key, value in results.items()}


def discover_cpp_method_wrapper_models(paths: list[Path], root: Path) -> tuple[dict[str, list[dict[str, Any]]], set[tuple[str, int]]]:
    models: dict[str, list[dict[str, Any]]] = defaultdict(list)
    factory_locations: set[tuple[str, int]] = set()
    method_pattern = re.compile(
        r"(?:^|[;}\n])\s*(?:template\s*<[^>]+>\s*)?(?:virtual\s+)?(?:[A-Za-z_][A-Za-z0-9_:<>,*&]*\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        re.MULTILINE,
    )
    for path in paths:
        for class_info in parse_cpp_classes(path, root):
            class_body = class_info["body"]
            type_aliases = cpp_type_aliases(class_info["text"])
            for method_match in method_pattern.finditer(class_body):
                method_name = method_match.group(1)
                signature_start = method_match.start(1)
                signature = extract_call_block(class_body, signature_start)
                parameters = [cpp_parameter_name(item) for item in split_arguments(signature)]
                signature_end = signature_start + len(signature)
                body_start = class_body.find("{", signature_end)
                if body_start == -1 or ";" in class_body[signature_end:body_start]:
                    continue
                method_body, _ = extract_braced_block(class_body, body_start)
                factories: list[tuple[str, str, int, str, int, str]] = []
                action_spans = []
                for match in re.finditer(r"rclcpp_action::create_(server|client)\s*<\s*([^;()]+?)\s*>\s*\(", method_body):
                    block = extract_call_block(method_body, match.start())
                    collection = "action_servers" if match.group(1) == "server" else "action_clients"
                    kind = "action_server" if match.group(1) == "server" else "action_client"
                    factories.append((collection, kind, 1, match.group(2).strip(), match.start(), block))
                    action_spans.append((match.start(), match.start() + len(block)))
                for collection, kind, call in (
                    ("publishers", "publisher", "create_publisher"),
                    ("subscriptions", "subscription", "create_subscription"),
                    ("service_servers", "service_server", "create_service"),
                    ("service_clients", "service_client", "create_client"),
                ):
                    for match in re.finditer(rf"(?<![A-Za-z0-9_]){call}\s*<\s*([^;()]+?)\s*>\s*\(", method_body):
                        if call == "create_client" and any(start <= match.start() <= end for start, end in action_spans):
                            continue
                        block = extract_call_block(method_body, match.start())
                        prefix = method_body[max(0, match.start() - 16) : match.start()]
                        name_index = 1 if re.search(r"rclcpp\s*::\s*$", prefix) else 0
                        factories.append((collection, kind, name_index, match.group(1).strip(), match.start(), block))
                for collection, kind, factory_name_index, factory_type, factory_offset, block in factories:
                    factory_arguments = split_arguments(block)
                    if len(factory_arguments) <= factory_name_index:
                        continue
                    factory_name = factory_arguments[factory_name_index].strip()
                    if factory_name not in parameters:
                        continue
                    absolute_offset = class_info["body_offset"] + body_start + factory_offset
                    line = line_number(class_info["text"], absolute_offset)
                    model = {
                        "class": class_info["name"],
                        "method": method_name,
                        "collection": collection,
                        "kind": kind,
                        "name_index": parameters.index(factory_name),
                        "type": resolve_cpp_type(factory_type, type_aliases),
                        "file": class_info["file"],
                        "line": line,
                        "evidence": [evidence(class_info["file"], line, "cpp_method_wrapper_factory", block)],
                    }
                    models[method_name].append(model)
                    factory_locations.add((class_info["file"], line))
    return models, factory_locations


def scan_cpp_method_wrapper_invocations(path: Path, root: Path, models: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    if not models:
        return {}
    text = read_text(path)
    rel_file = relative(path, root)
    class_ranges = parse_cpp_classes(path, root)

    def enclosing_class(offset: int) -> str | None:
        match = next((item for item in class_ranges if item["body_offset"] <= offset < item["end"]), None)
        return match["name"] if match else None

    results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for method_name, method_models in models.items():
        pattern = re.compile(rf"(?:->|\.)\s*{re.escape(method_name)}\s*\(")
        for match in pattern.finditer(text):
            block = extract_call_block(text, match.start())
            arguments = split_arguments(block)
            for model in method_models:
                if rel_file == model["file"] and line_number(text, match.start()) == model["line"]:
                    continue
                if len(arguments) <= model["name_index"]:
                    continue
                name, confidence, dynamic = cpp_value(arguments[model["name_index"]])
                if dynamic:
                    continue
                line = line_number(text, match.start())
                results[model["collection"]].append(
                    finding(
                        model["kind"],
                        name,
                        model["type"],
                        rel_file,
                        line,
                        "cpp_method_wrapper_resolution",
                        block,
                        confidence=min(confidence, 0.9),
                        resolved=True,
                        wrapper={"class": model["class"], "method": method_name, "factory_evidence": model["evidence"]},
                        class_name=enclosing_class(match.start()),
                    )
                )
    return {key: unique_findings(value) for key, value in results.items()}


def cmake_testing_context(text: str, offset: int) -> bool:
    stack: list[bool] = []
    for match in re.finditer(r"(?im)^\s*(if|endif)\s*\(([^)]*)\)", text):
        if match.start() >= offset:
            break
        if match.group(1).casefold() == "if":
            inherited = stack[-1] if stack else False
            condition = match.group(2).upper()
            testing_guard = "BUILD_TESTING" in condition and not re.search(r"\bNOT\s+\$?\{?BUILD_TESTING\}?", condition)
            stack.append(inherited or testing_guard)
        elif stack:
            stack.pop()
    return stack[-1] if stack else False


def cmake_entity_scope(text: str, offset: int, rel_file: str, package_name: str | None = None, name: str | None = None, sources: Iterable[str] = ()) -> str:
    scopes = {deployment_scope(rel_file, package_name), deployment_scope(name, package_name)}
    scopes.update(deployment_scope(source, package_name) for source in sources)
    if cmake_testing_context(text, offset) or "test" in scopes:
        return "test"
    if "example" in scopes:
        return "example"
    return "production"


def scan_cmake(path: Path, root: Path, package_name: str | None = None) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], set[str]]:
    text = read_text(path)
    rel_file = relative(path, root)
    executables = []
    variables = {match.group(1): match.group(2).strip('"\'') for match in re.finditer(r"\bset\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s+([^\s\)]+)", text)}
    project_match = re.search(r"\bproject\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)", text)
    if project_match:
        variables["PROJECT_NAME"] = project_match.group(1)
    targets = set()
    for match in re.finditer(r"\badd_executable\s*\(\s*([^\s\)]+)", text):
        name = match.group(1)
        variable = re.fullmatch(r"\$\{([^}]+)\}", name)
        if variable:
            name = variables.get(variable.group(1), name)
        targets.add(name)
        block = extract_call_block(text, match.start())
        cmake_tokens = re.findall(r"[^\s\)]+", block[block.find("(") + 1 : block.rfind(")")])
        sources = [token.strip('"\'') for token in cmake_tokens[1:] if not token.startswith("$")]
        scope = cmake_entity_scope(text, match.start(), rel_file, package_name, name, sources)
        executables.append(finding("executable", name, "cmake", rel_file, line_number(text, match.start()), "cmake_parser", block, sources=sources, deployment_scope=scope))
    installed = set()
    for match in re.finditer(r"\binstall\s*\(\s*TARGETS\s+([^\)]*)\)", text, re.DOTALL | re.IGNORECASE):
        before_destination = re.split(r"\b(?:DESTINATION|EXPORT|ARCHIVE|LIBRARY|RUNTIME|INCLUDES)\b", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        for token in re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_-]*", before_destination):
            variable = re.fullmatch(r"\$\{([^}]+)\}", token)
            installed.add(variables.get(variable.group(1), token) if variable else token)
    dependencies: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in re.finditer(r"\bfind_package\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)", text):
        reference = match.group(1)
        scope = cmake_entity_scope(text, match.start(), rel_file, package_name, reference)
        extractor = "cmake_find_package" if scope == "production" else f"cmake_find_package_{scope}"
        dependencies[reference].append(evidence(rel_file, line_number(text, match.start()), extractor, match.group(0)))
    return executables, dict(dependencies), {target for target in targets if target not in installed}


def scan_python_setup(path: Path, root: Path) -> list[dict[str, Any]]:
    text = read_text(path)
    rel_file = relative(path, root)
    results = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree:
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "console_scripts" and isinstance(value, (ast.List, ast.Tuple)):
                        for entry in value.elts:
                            if isinstance(entry, ast.Constant) and isinstance(entry.value, str) and "=" in entry.value:
                                name = entry.value.split("=", 1)[0].strip()
                                target = entry.value.split("=", 1)[1].strip()
                                results.append(finding("executable", name, "python_entry_point", rel_file, entry.lineno, "python_ast", entry.value, target=target))
    return unique_findings(results)


def executable_finding(name: str, target: Any, path: Path, root: Path, extractor: str) -> dict[str, Any]:
    text = read_text(path)
    target_text = target.get("call") if isinstance(target, dict) else str(target)
    name_match = re.search(rf"^\s*['\"]?{re.escape(name)}['\"]?\s*=", text, re.MULTILINE)
    line = line_number(text, name_match.start()) if name_match else 1
    return finding(
        "executable",
        name,
        "python_entry_point",
        relative(path, root),
        line,
        extractor,
        name_match.group(0) if name_match else f"{name} = {target_text}",
        target=target_text,
    )


def scan_python_pyproject(path: Path, root: Path) -> list[dict[str, Any]]:
    try:
        data = tomllib.loads(read_text(path))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    entries: dict[str, Any] = {}
    project = data.get("project", {})
    for section in (project.get("scripts", {}), project.get("gui-scripts", {}), project.get("entry-points", {}).get("console_scripts", {})):
        if isinstance(section, dict):
            entries.update(section)
    poetry_scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
    if isinstance(poetry_scripts, dict):
        entries.update(poetry_scripts)
    return unique_findings(executable_finding(name, target, path, root, "pyproject_toml") for name, target in entries.items())


def scan_python_setup_cfg(path: Path, root: Path) -> list[dict[str, Any]]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(read_text(path))
    except configparser.Error:
        return []
    if not parser.has_section("options.entry_points"):
        return []
    results = []
    for group in ("console_scripts", "gui_scripts"):
        if not parser.has_option("options.entry_points", group):
            continue
        for entry in parser.get("options.entry_points", group).splitlines():
            if "=" not in entry:
                continue
            name, target = (item.strip() for item in entry.split("=", 1))
            if name and target:
                results.append(executable_finding(name, target, path, root, "setup_cfg"))
    return unique_findings(results)


def parse_interface(path: Path, root: Path) -> dict[str, Any]:
    text = read_text(path)
    sections: list[list[dict[str, Any]]] = [[]]
    for line_index, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "---":
            sections.append([])
            continue
        match = re.match(r"([^\s]+)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(.+))?$", line)
        if match:
            sections[-1].append({"type": match.group(1), "name": match.group(2), "constant": match.group(3), "line": line_index})
    labels = {".msg": ["message"], ".srv": ["request", "response"], ".action": ["goal", "result", "feedback"]}[path.suffix]
    return {
        "kind": labels[0] if len(labels) == 1 else path.suffix[1:],
        "name": path.stem,
        "file": relative(path, root),
        "sections": [{"name": labels[index] if index < len(labels) else f"section_{index + 1}", "fields": fields} for index, fields in enumerate(sections)],
        "fact_type": "detected",
        "confidence": 1.0,
        "evidence": [evidence(relative(path, root), 1, "ros_interface_parser", path.name)],
    }


def strip_yaml_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "#":
            return line[:index]
    return line


def split_yaml_mapping(value: str) -> tuple[str, str] | None:
    quote: str | None = None
    depth = 0
    for index, character in enumerate(value):
        if quote:
            if character == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth = max(0, depth - 1)
        elif character == ":" and depth == 0:
            return value[:index].strip().strip('"\''), value[index + 1 :].strip()
    return None


def parse_yaml_scalar(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"[-+]?\d+", stripped):
        return int(stripped)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", stripped):
        return float(stripped)
    if stripped[0:1] in {'"', "'"} and stripped[-1:] == stripped[0]:
        try:
            return ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            return stripped[1:-1]
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1]
        return [parse_yaml_scalar(item) for item in split_cpp_template_arguments(inner)] if inner.strip() else []
    if stripped.startswith("{") and stripped.endswith("}"):
        result = {}
        for item in split_cpp_template_arguments(stripped[1:-1]):
            mapping = split_yaml_mapping(item)
            if mapping:
                result[mapping[0]] = parse_yaml_scalar(mapping[1])
        return result
    return stripped


def parse_yaml_structure(text: str) -> tuple[Any, dict[tuple[str, ...], int]]:
    records = []
    for line_number_value, raw_line in enumerate(text.splitlines(), start=1):
        without_comment = strip_yaml_comment(raw_line).rstrip()
        if not without_comment.strip() or without_comment.strip() in {"---", "..."}:
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        records.append((indent, without_comment.strip(), line_number_value))
    line_map: dict[tuple[str, ...], int] = {}

    def parse_block(index: int, indent: int, path: tuple[str, ...]) -> tuple[Any, int]:
        is_list = records[index][1].startswith("- ")
        container: Any = [] if is_list else {}
        while index < len(records):
            current_indent, content, source_line = records[index]
            if current_indent < indent or current_indent > indent or content.startswith("- ") != is_list:
                break
            if is_list:
                remainder = content[2:].strip()
                item_path = path + (str(len(container)),)
                mapping = split_yaml_mapping(remainder)
                if mapping:
                    item = {}
                    key, raw_value = mapping
                    line_map[item_path + (key,)] = source_line
                    if raw_value:
                        item[key] = parse_yaml_scalar(raw_value)
                        index += 1
                    elif index + 1 < len(records) and records[index + 1][0] > current_indent:
                        item[key], index = parse_block(index + 1, records[index + 1][0], item_path + (key,))
                    else:
                        item[key] = None
                        index += 1
                    while index < len(records) and records[index][0] > current_indent:
                        nested_indent, nested_content, nested_line = records[index]
                        nested_mapping = split_yaml_mapping(nested_content)
                        if not nested_mapping:
                            break
                        nested_key, nested_raw = nested_mapping
                        line_map[item_path + (nested_key,)] = nested_line
                        if nested_raw:
                            item[nested_key] = parse_yaml_scalar(nested_raw)
                            index += 1
                        elif index + 1 < len(records) and records[index + 1][0] > nested_indent:
                            item[nested_key], index = parse_block(index + 1, records[index + 1][0], item_path + (nested_key,))
                        else:
                            item[nested_key] = None
                            index += 1
                    container.append(item)
                else:
                    container.append(parse_yaml_scalar(remainder))
                    line_map[item_path] = source_line
                    index += 1
                continue
            mapping = split_yaml_mapping(content)
            if not mapping:
                index += 1
                continue
            key, raw_value = mapping
            key_path = path + (key,)
            line_map[key_path] = source_line
            if raw_value:
                container[key] = parse_yaml_scalar(raw_value)
                index += 1
            elif index + 1 < len(records) and records[index + 1][0] > current_indent:
                container[key], index = parse_block(index + 1, records[index + 1][0], key_path)
            else:
                container[key] = None
                index += 1
        return container, index

    if not records:
        return {}, line_map
    data, _ = parse_block(0, records[0][0], ())
    return data, line_map


def yaml_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "double"
    if isinstance(value, list):
        element_types = sorted({yaml_value_type(item) for item in value})
        return f"array<{element_types[0]}>" if len(element_types) == 1 else "array<mixed>"
    if isinstance(value, dict):
        return "map"
    return "string"


def parameter_selector(path: tuple[str, ...]) -> tuple[str, str, int]:
    selector = "/".join(part.strip("/") for part in path if part)
    if path and str(path[0]).startswith("/"):
        selector = "/" + selector
    if selector == "**" or selector.endswith("/**"):
        selector = "/**" if selector == "**" else selector
    namespace = selector.rsplit("/", 1)[0] if "/" in selector.strip("/") else ""
    specificity = 0 if selector == "/**" else 1 if "*" in selector else 2
    return selector or "/**", namespace, specificity


def scan_parameter_yaml(path: Path, root: Path) -> list[dict[str, Any]]:
    text = read_text(path)
    if "ros__parameters" not in text:
        return []
    data, line_map = parse_yaml_structure(text)
    rel_file = relative(path, root)
    results = []

    def flatten_parameters(value: Any, prefix: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Any]]:
        if isinstance(value, dict):
            for key, nested in value.items():
                yield from flatten_parameters(nested, prefix + (str(key),))
        else:
            yield prefix, value

    def visit(value: Any, path_parts: tuple[str, ...]) -> None:
        if not isinstance(value, dict):
            return
        if "ros__parameters" in value and isinstance(value["ros__parameters"], dict):
            selector, namespace, specificity = parameter_selector(path_parts)
            for parameter_path, parameter_value in flatten_parameters(value["ros__parameters"], ()):
                full_path = path_parts + ("ros__parameters",) + parameter_path
                source_line = line_map.get(full_path, line_map.get(path_parts + ("ros__parameters",), 1))
                name = ".".join(parameter_path)
                results.append(
                    finding(
                        "parameter_override",
                        name,
                        yaml_value_type(parameter_value),
                        rel_file,
                        source_line,
                        "yaml_parameter_tree",
                        text.splitlines()[source_line - 1] if 0 < source_line <= len(text.splitlines()) else name,
                        value=parameter_value,
                        selector=selector,
                        namespace=namespace,
                        parameter_path=list(parameter_path),
                        selector_specificity=specificity,
                        precedence_rank=20 + specificity,
                        confidence=0.96,
                    )
                )
        for key, nested in value.items():
            if key != "ros__parameters":
                visit(nested, path_parts + (str(key),))

    visit(data, ())
    return unique_findings(results)


def xml_attributes(fragment: str) -> dict[str, str]:
    return {
        match.group(1): html_lib.unescape(match.group(3))
        for match in re.finditer(r"([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*(['\"])(.*?)\2", fragment, re.DOTALL)
    }


def plugin_role(base_class_type: str | None, *identifiers: str | None) -> tuple[str, str]:
    base = (base_class_type or "").casefold()
    combined = " ".join(str(value or "") for value in identifiers)
    if "hardware_interface::actuatorinterface" in base:
        return "ros2_control actuator hardware", "hardware_actuator"
    if "hardware_interface::sensorinterface" in base:
        return "ros2_control sensor hardware", "hardware_sensor"
    if "hardware_interface::systeminterface" in base:
        return "ros2_control system hardware", "hardware_system"
    if "controller_interface::chainablecontrollerinterface" in base:
        return "ros2_control chainable controller", "controller"
    if "controller_interface::controllerinterface" in base:
        return "ros2_control controller", "controller"
    if "transmission_interface::transmissionloader" in base:
        return "ros2_control transmission loader", "transmission"
    role = infer_algorithm_role(combined)
    return role, "algorithm" if role != "runtime plugin" else "plugin"


def scan_plugins(path: Path, root: Path) -> list[dict[str, Any]]:
    text = read_text(path)
    rel_file = relative(path, root)
    results = []
    if path.suffix in {".yaml", ".yml"}:
        for match in re.finditer(r"^\s*plugin\s*:\s*['\"]?([^'\"#\n]+)", text, re.MULTILINE):
            name = match.group(1).strip()
            role, category = plugin_role(None, name)
            results.append(finding("plugin", name, name, rel_file, line_number(text, match.start()), "yaml_plugin", match.group(0), confidence=0.96, role=role, plugin_category=category))
    elif path.suffix == ".xml":
        for match in re.finditer(r"<class\b([^>]*)>", text, re.DOTALL):
            attributes = xml_attributes(match.group(1))
            class_type = attributes.get("type")
            name = attributes.get("name") or class_type
            if not name:
                continue
            base_class_type = attributes.get("base_class_type")
            role, category = plugin_role(base_class_type, name, class_type)
            dynamic = any(token in str(value) for value in (name, class_type, base_class_type) for token in ("${", "$("))
            results.append(
                finding(
                    "plugin",
                    name,
                    class_type,
                    rel_file,
                    line_number(text, match.start()),
                    "pluginlib_xml",
                    match.group(0),
                    confidence=0.98 if not dynamic else 0.72,
                    resolved=not dynamic,
                    role=role,
                    plugin_category=category,
                    base_class_type=base_class_type,
                )
            )
    return results


def infer_algorithm_role(name: str) -> str:
    lowered = name.lower()
    roles = [
        ("planner", "planning"),
        ("controller", "control"),
        ("localiz", "localization"),
        ("slam", "mapping"),
        ("filter", "filtering"),
        ("costmap", "environment model"),
        ("behavior", "behavior execution"),
        ("dock", "docking"),
        ("driver", "hardware driver"),
        ("hardware", "hardware interface"),
        ("kinematic", "kinematics"),
    ]
    return next((role for token, role in roles if token in lowered), "runtime plugin")


def launch_expression(node: ast.AST | None, constants: dict[str, Any]) -> tuple[str | None, float, bool]:
    if node is None:
        return None, 1.0, False
    value, confidence, dynamic = evaluate_python_expression(node, constants)
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True), confidence, dynamic
    return str(value) if value is not None else None, confidence, dynamic


def keyword_node(node: ast.Call, name: str) -> ast.AST | None:
    return next((keyword.value for keyword in node.keywords if keyword.arg == name), None)


def list_pairs(node: ast.AST | None, constants: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    pairs = []
    for item in node.elts:
        if isinstance(item, (ast.List, ast.Tuple)) and len(item.elts) >= 2:
            source, _, source_dynamic = evaluate_python_expression(item.elts[0], constants)
            target, _, target_dynamic = evaluate_python_expression(item.elts[1], constants)
            pairs.append({"from": str(source), "to": str(target), "resolved": not (source_dynamic or target_dynamic)})
    return pairs


def parameter_references(node: ast.AST | None, constants: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    results = []
    for item in node.elts:
        value, confidence, dynamic = evaluate_python_expression(item, constants)
        if isinstance(value, dict):
            results.append({"kind": "inline", "value": value, "confidence": confidence, "resolved": not dynamic})
        else:
            results.append({"kind": "file" if str(value).endswith((".yaml", ".yml")) else "expression", "value": str(value), "confidence": confidence, "resolved": not dynamic})
    return results


def scan_launch_python(path: Path, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    text = read_text(path)
    rel_file = relative(path, root)
    try:
        tree = ast.parse(text, filename=rel_file)
    except SyntaxError as exc:
        return [], [], [], [diagnostic("RD004", "error", "Python launch file could not be parsed", f"{rel_file}: {exc.msg}.", [evidence(rel_file, exc.lineno, "python_ast", exc.text or "")], 1.0)]
    constants: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value, _, dynamic = evaluate_python_expression(node.value, constants)
            if isinstance(value, (str, int, float, bool, list, dict)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value
    actions: list[dict[str, Any]] = []
    includes: list[dict[str, Any]] = []
    arguments: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        short = call_name(node.func).split(".")[-1]
        if short in {"Node", "LifecycleNode", "ComposableNode", "ComposableNodeContainer"}:
            package_node = keyword_node(node, "package")
            executable_node = keyword_node(node, "executable") or keyword_node(node, "plugin")
            name_node = keyword_node(node, "name")
            namespace_node = keyword_node(node, "namespace")
            package, package_confidence, package_dynamic = launch_expression(package_node, constants)
            executable, executable_confidence, executable_dynamic = launch_expression(executable_node, constants)
            name, name_confidence, name_dynamic = launch_expression(name_node, constants)
            namespace, namespace_confidence, namespace_dynamic = launch_expression(namespace_node, constants)
            confidence = min(value for value in [package_confidence, executable_confidence] if value > 0) if package_node is not None and executable_node is not None else 0.75
            actions.append(
                {
                    "kind": "container" if short == "ComposableNodeContainer" else "composable_node" if short == "ComposableNode" else "node",
                    "package": package,
                    "executable": executable,
                    "name": name,
                    "namespace": namespace,
                    "condition": expression_text(keyword_node(node, "condition")),
                    "remappings": list_pairs(keyword_node(node, "remappings"), constants),
                    "parameters": parameter_references(keyword_node(node, "parameters"), constants),
                    "lifecycle": short == "LifecycleNode",
                    "composed": short in {"ComposableNode", "ComposableNodeContainer"},
                    "fact_type": "detected",
                    "confidence": round(confidence, 2),
                    "resolved": not (package_dynamic or executable_dynamic or name_dynamic or namespace_dynamic),
                    "source_file": rel_file,
                    "line": node.lineno,
                    "evidence": [evidence(rel_file, node.lineno, "python_launch_ast", source_segment(text, node))],
                }
            )
        elif short == "IncludeLaunchDescription":
            target_node = node.args[0] if node.args else None
            target, confidence, dynamic = launch_expression(target_node, constants)
            launch_arguments = keyword_node(node, "launch_arguments")
            includes.append(
                {
                    "target": target,
                    "resolved_path": None,
                    "exists": None,
                    "arguments": expression_text(launch_arguments),
                    "condition": expression_text(keyword_node(node, "condition")),
                    "fact_type": "detected",
                    "confidence": round(confidence, 2),
                    "resolved": not dynamic,
                    "source_file": rel_file,
                    "line": node.lineno,
                    "evidence": [evidence(rel_file, node.lineno, "python_launch_ast", source_segment(text, node))],
                }
            )
        elif short == "DeclareLaunchArgument":
            name, confidence, dynamic = launch_expression(node.args[0] if node.args else None, constants)
            default, _, default_dynamic = launch_expression(keyword_node(node, "default_value"), constants)
            arguments.append({"name": name, "default": default, "description": launch_expression(keyword_node(node, "description"), constants)[0], "fact_type": "detected", "confidence": confidence, "resolved": not (dynamic or default_dynamic), "source_file": rel_file, "line": node.lineno, "evidence": [evidence(rel_file, node.lineno, "python_launch_ast", source_segment(text, node))]})
        elif short in {"PushRosNamespace", "SetRemap"}:
            value, confidence, dynamic = launch_expression(node.args[0] if node.args else None, constants)
            actions.append({"kind": "namespace" if short == "PushRosNamespace" else "remap", "value": value, "source_file": rel_file, "line": node.lineno, "fact_type": "detected", "confidence": confidence, "resolved": not dynamic, "evidence": [evidence(rel_file, node.lineno, "python_launch_ast", source_segment(text, node))]})
    return actions, includes, arguments, []


def xml_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def scan_launch_xml(path: Path, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rel_file = relative(path, root)
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return [], [], [], [diagnostic("RD005", "error", "XML launch file could not be parsed", f"{rel_file}: {exc}.", [evidence(rel_file, getattr(exc, "position", (None,))[0], "xml_parser", str(exc))], 1.0)]
    actions = []
    includes = []
    arguments = []
    for element in tree.getroot().iter():
        tag = xml_tag(element)
        line = getattr(element, "sourceline", None)
        if tag in {"node", "composable_node", "node_container"}:
            remappings = [{"from": child.attrib.get("from"), "to": child.attrib.get("to"), "resolved": True} for child in element if xml_tag(child) == "remap"]
            parameters = []
            for child in element:
                if xml_tag(child) == "param":
                    value = child.attrib.get("from") or child.attrib.get("value")
                    parameters.append({"kind": "file" if value and value.endswith((".yaml", ".yml")) else "inline", "value": value, "name": child.attrib.get("name"), "resolved": "$" not in (value or ""), "confidence": 0.95})
            actions.append({"kind": "container" if tag == "node_container" else tag, "package": element.attrib.get("pkg") or element.attrib.get("package"), "executable": element.attrib.get("exec") or element.attrib.get("executable") or element.attrib.get("plugin"), "name": element.attrib.get("name"), "namespace": element.attrib.get("namespace") or element.attrib.get("ns"), "condition": element.attrib.get("if") or element.attrib.get("unless"), "remappings": remappings, "parameters": parameters, "lifecycle": element.attrib.get("lifecycle") == "true", "composed": tag in {"composable_node", "node_container"}, "fact_type": "detected", "confidence": 0.98, "resolved": not any("$" in (value or "") for value in [element.attrib.get("pkg"), element.attrib.get("exec"), element.attrib.get("name")]), "source_file": rel_file, "line": line, "evidence": [evidence(rel_file, line, "xml_launch_parser", ET.tostring(element, encoding="unicode"))]})
        elif tag == "include":
            target = element.attrib.get("file")
            includes.append({"target": target, "resolved_path": None, "exists": None, "arguments": {child.attrib.get("name"): child.attrib.get("value") for child in element if xml_tag(child) == "arg"}, "condition": element.attrib.get("if") or element.attrib.get("unless"), "fact_type": "detected", "confidence": 0.98, "resolved": "$" not in (target or ""), "source_file": rel_file, "line": line, "evidence": [evidence(rel_file, line, "xml_launch_parser", ET.tostring(element, encoding="unicode"))]})
        elif tag == "arg" and element.attrib.get("name"):
            arguments.append({"name": element.attrib.get("name"), "default": element.attrib.get("default"), "description": element.attrib.get("description"), "fact_type": "detected", "confidence": 0.98, "resolved": "$" not in (element.attrib.get("default") or ""), "source_file": rel_file, "line": line, "evidence": [evidence(rel_file, line, "xml_launch_parser", ET.tostring(element, encoding="unicode"))]})
        elif tag == "push_ros_namespace":
            actions.append({"kind": "namespace", "value": element.attrib.get("namespace"), "source_file": rel_file, "line": line, "fact_type": "detected", "confidence": 0.98, "resolved": "$" not in (element.attrib.get("namespace") or ""), "evidence": [evidence(rel_file, line, "xml_launch_parser", ET.tostring(element, encoding="unicode"))]})
    return actions, includes, arguments, []


def yaml_scalar(value: str) -> str:
    return value.strip().strip("[]{} ").strip('"\'')


def scan_launch_yaml(path: Path, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse standard ROS YAML launch actions without requiring PyYAML."""
    lines = read_text(path).splitlines()
    rel_file = relative(path, root)
    action_pattern = re.compile(r"^(\s*)-\s*(node|composable_node|node_container|include|arg|push_ros_namespace)\s*:\s*(.*)$")
    records = []
    for index, raw_line in enumerate(lines):
        match = action_pattern.match(raw_line)
        if not match:
            continue
        indent = len(match.group(1))
        end = index + 1
        while end < len(lines):
            next_line = lines[end]
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_line.strip() and next_indent <= indent and action_pattern.match(next_line):
                break
            end += 1
        block = lines[index:end]
        values: dict[str, str] = {}
        remappings = []
        parameters = []
        for offset, line in enumerate(block):
            key_match = re.match(r"\s*-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$", line)
            if key_match and key_match.group(2):
                key = key_match.group(1)
                value = yaml_scalar(key_match.group(2).split("#", 1)[0])
                values[key] = value
                if key in {"param", "params", "parameters", "file"} and value.endswith((".yaml", ".yml")):
                    parameters.append({"kind": "file", "value": value, "confidence": 0.9, "resolved": "$" not in value})
            remap_match = re.search(r"(?:from|src)\s*:\s*['\"]?([^,'\"}]+).*?(?:to|dst)\s*:\s*['\"]?([^,'\"}]+)", line)
            if remap_match:
                remappings.append({"from": remap_match.group(1).strip(), "to": remap_match.group(2).strip(), "resolved": True})
        records.append((match.group(2), index + 1, values, remappings, parameters, "\n".join(block)))
    actions = []
    includes = []
    arguments = []
    for kind, line, values, remappings, parameters, block in records:
        if kind in {"node", "composable_node", "node_container"}:
            actions.append({"kind": "container" if kind == "node_container" else kind, "package": values.get("pkg") or values.get("package"), "executable": values.get("exec") or values.get("executable") or values.get("plugin"), "name": values.get("name"), "namespace": values.get("namespace") or values.get("ns"), "condition": values.get("if") or values.get("unless"), "remappings": remappings, "parameters": parameters, "lifecycle": values.get("lifecycle", "false").lower() == "true", "composed": kind != "node", "fact_type": "detected", "confidence": 0.86, "resolved": not any("$" in value for value in values.values()), "source_file": rel_file, "line": line, "evidence": [evidence(rel_file, line, "yaml_launch_parser", block)]})
        elif kind == "include":
            target = values.get("file") or values.get("path")
            includes.append({"target": target, "resolved_path": None, "exists": None, "arguments": {}, "condition": values.get("if") or values.get("unless"), "fact_type": "detected", "confidence": 0.86, "resolved": "$" not in (target or ""), "source_file": rel_file, "line": line, "evidence": [evidence(rel_file, line, "yaml_launch_parser", block)]})
        elif kind == "arg":
            arguments.append({"name": values.get("name"), "default": values.get("default"), "description": values.get("description"), "fact_type": "detected", "confidence": 0.86, "resolved": "$" not in values.get("default", ""), "source_file": rel_file, "line": line, "evidence": [evidence(rel_file, line, "yaml_launch_parser", block)]})
        elif kind == "push_ros_namespace":
            actions.append({"kind": "namespace", "value": values.get("namespace") or values.get("value"), "source_file": rel_file, "line": line, "fact_type": "detected", "confidence": 0.86, "resolved": False, "evidence": [evidence(rel_file, line, "yaml_launch_parser", block)]})
    return actions, includes, arguments, []


def resolve_repository_reference(
    expression: str | None,
    source_file: str,
    root: Path,
    packages: list[dict[str, Any]],
    repository_files: Iterable[Path],
) -> tuple[str | None, bool | None]:
    if not expression or "$(var " in expression or "LaunchConfiguration" in expression:
        return None, None
    value = expression
    package_match = re.search(r"\$\(find-pkg-share\s+([^\)]+)\)", value)
    if package_match:
        package = next((item for item in packages if item["name"] == package_match.group(1)), None)
        if not package:
            return None, None
        value = value.replace(package_match.group(0), str(root / package["path"]))
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / Path(source_file).parent / candidate
    if candidate.exists():
        return relative(candidate, root), True
    basename = Path(value).name
    matches = [path for path in repository_files if path.name == basename]
    if len(matches) == 1:
        return relative(matches[0], root), True
    return relative(candidate, root), False


def scan_launch_file(path: Path, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if path.name.endswith(".launch.py"):
        return scan_launch_python(path, root)
    if path.name.endswith((".launch.yaml", ".launch.yml")):
        return scan_launch_yaml(path, root)
    return scan_launch_xml(path, root)


def control_parameters(fragment: str) -> dict[str, str]:
    return {
        attributes.get("name", ""): html_lib.unescape(match.group(2).strip())
        for match in re.finditer(r"<param\b([^>]*)>(.*?)</param>", fragment, re.DOTALL)
        if (attributes := xml_attributes(match.group(1))).get("name")
    }


XACRO_MACRO_PATTERN = re.compile(r"<xacro:macro\b([^>]*)>(.*?)</xacro:macro\s*>", re.DOTALL)
XACRO_INVOCATION_PATTERN = re.compile(r"<xacro:([A-Za-z_][A-Za-z0-9_.-]*)\b([^>]*?)(?:/\s*>|>(.*?)</xacro:\1\s*>)", re.DOTALL)
XACRO_INCLUDE_PATTERN = re.compile(r"<xacro:include\b([^>]*?)(?:/\s*>|>.*?</xacro:include\s*>)", re.DOTALL)


@dataclass
class XacroRegistry:
    macros: dict[str, list[dict[str, Any]]]
    includes: dict[str, list[str]]


def mask_xacro_macro_definitions(text: str) -> str:
    return XACRO_MACRO_PATTERN.sub(lambda match: "".join("\n" if character == "\n" else " " for character in match.group(0)), text)


def xacro_parameter_defaults(specification: str) -> dict[str, str | None]:
    try:
        tokens = shlex.split(specification)
    except ValueError:
        tokens = specification.split()
    parameters = {}
    for token in tokens:
        token = token.removeprefix("^|").lstrip("*")
        if not token:
            continue
        name, separator, default = token.partition(":=")
        parameters[name] = default if separator else None
    return parameters


def resolve_xacro_include(
    filename: str,
    consumer_file: str,
    known_files: set[str],
    package_paths: dict[str, list[str]],
) -> list[str]:
    candidates = [html_lib.unescape(filename.strip())]
    package_expression = re.compile(r"\$\((?:find|find-pkg-share)\s+([^\s)]+)\)")
    expanded = []
    for candidate in candidates:
        match = package_expression.search(candidate)
        if not match:
            expanded.append((candidate, False))
            continue
        for package_path in package_paths.get(match.group(1), []):
            expanded.append((package_expression.sub(package_path, candidate, count=1), True))
    resolved = []
    for candidate, root_relative in expanded:
        if "${" in candidate or "$(" in candidate or any(character in candidate for character in "*?["):
            continue
        normalized = posixpath.normpath(
            candidate.lstrip("/") if root_relative else posixpath.join(posixpath.dirname(consumer_file), candidate)
        )
        if normalized in known_files and normalized not in resolved:
            resolved.append(normalized)
    return resolved


def discover_xacro_macros(paths: Iterable[Path], root: Path, packages: Iterable[dict[str, Any]]) -> XacroRegistry:
    macros: dict[str, list[dict[str, Any]]] = defaultdict(list)
    includes: dict[str, list[str]] = defaultdict(list)
    xacro_paths = [path for path in paths if path.suffix == ".xacro"]
    known_files = {relative(path, root) for path in xacro_paths}
    package_paths: dict[str, list[str]] = defaultdict(list)
    for package in packages:
        package_paths[package["name"]].append(package["path"])
    for path in xacro_paths:
        text = read_text(path)
        rel_file = relative(path, root)
        for match in XACRO_INCLUDE_PATTERN.finditer(text):
            filename = xml_attributes(match.group(1)).get("filename")
            if filename:
                includes[rel_file].extend(resolve_xacro_include(filename, rel_file, known_files, package_paths))
        for match in XACRO_MACRO_PATTERN.finditer(text):
            attributes = xml_attributes(match.group(1))
            name = attributes.get("name")
            if not name:
                continue
            macros[name].append(
                {
                    "name": name,
                    "parameters": xacro_parameter_defaults(attributes.get("params", "")),
                    "body": match.group(2),
                    "file": rel_file,
                    "line": line_number(text, match.start()),
                    "body_line": line_number(text, match.start(2)),
                    "snippet": match.group(0),
                }
            )
    return XacroRegistry(dict(macros), {name: list(dict.fromkeys(values)) for name, values in includes.items()})


def xacro_document_values(text: str) -> dict[str, str]:
    values = {}
    for match in re.finditer(r"<xacro:(?:property|arg)\b([^>]*)/?>", text):
        attributes = xml_attributes(match.group(1))
        name = attributes.get("name")
        value = attributes.get("value", attributes.get("default"))
        if name and value is not None:
            values[name] = value
    return values


def substitute_xacro_values(value: str, values: dict[str, str]) -> str:
    result = value
    for _ in range(6):
        previous = result
        result = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: values.get(match.group(1), match.group(0)), result)
        result = re.sub(r"\$\(arg\s+([A-Za-z_][A-Za-z0-9_]*)\)", lambda match: values.get(match.group(1), match.group(0)), result)
        if result == previous:
            break
    return result


def xacro_include_order(consumer_file: str, registry: XacroRegistry) -> list[str]:
    ordered = []
    visited = set()

    def visit(source_file: str) -> None:
        for included_file in registry.includes.get(source_file, []):
            if included_file in visited:
                continue
            visited.add(included_file)
            visit(included_file)
            ordered.append(included_file)

    visit(consumer_file)
    return ordered


def select_xacro_macro(
    name: str,
    consumer_file: str,
    registry: XacroRegistry,
    visibility_root: str | None = None,
) -> dict[str, Any] | None:
    candidates = registry.macros.get(name, [])
    local = [item for item in candidates if item["file"] == consumer_file]
    if local:
        return max(local, key=lambda item: item["line"])
    visible_order = xacro_include_order(visibility_root or consumer_file, registry)
    visible_rank = {file: index for index, file in enumerate(visible_order)}
    visible = [item for item in candidates if item["file"] in visible_rank]
    if visible:
        return max(visible, key=lambda item: (visible_rank[item["file"]], item["line"]))
    return None


def render_xacro_macro(
    name: str,
    invocation_attributes: dict[str, str],
    consumer_file: str,
    registry: XacroRegistry,
    inherited_values: dict[str, str],
    depth: int = 0,
    visibility_root: str | None = None,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    visibility_root = visibility_root or consumer_file
    definition = select_xacro_macro(name, consumer_file, registry, visibility_root)
    if definition is None or depth >= 6:
        return None, definition, False
    values = dict(inherited_values)
    for parameter, default in definition["parameters"].items():
        if default is not None:
            values[parameter] = substitute_xacro_values(default, values)
    values.update({key: substitute_xacro_values(value, values) for key, value in invocation_attributes.items()})
    values.update({key: substitute_xacro_values(value, values) for key, value in xacro_document_values(definition["body"]).items()})
    rendered = substitute_xacro_values(definition["body"], values)

    def expand_nested(match: re.Match[str]) -> str:
        nested_name = match.group(1)
        nested, _, _ = render_xacro_macro(
            nested_name,
            xml_attributes(match.group(2)),
            definition["file"],
            registry,
            values,
            depth + 1,
            visibility_root,
        )
        return nested if nested is not None else match.group(0)

    rendered = XACRO_INVOCATION_PATTERN.sub(expand_nested, rendered)
    resolved = not re.search(r"\$\{|\$\(arg\s|<xacro:[A-Za-z_]", rendered)
    return rendered, definition, resolved


def expand_xacro_control_fragments(text: str, rel_file: str, registry: XacroRegistry) -> list[dict[str, Any]]:
    fragments = []
    values = xacro_document_values(text)
    source = mask_xacro_macro_definitions(text)
    for match in XACRO_INVOCATION_PATTERN.finditer(source):
        rendered, definition, resolved = render_xacro_macro(match.group(1), xml_attributes(match.group(2)), rel_file, registry, values)
        if rendered is None or definition is None or not re.search(r"<(?:ros2_control|transmission)\b", rendered):
            continue
        invocation_line = line_number(text, match.start())
        fragments.append(
            {
                "text": rendered,
                "file": definition["file"],
                "base_line": definition["body_line"],
                "source": "xacro",
                "confidence": 0.92 if resolved else 0.72,
                "resolved": resolved,
                "extra_evidence": [
                    evidence(rel_file, invocation_line, "xacro_macro_invocation", match.group(0)),
                    evidence(definition["file"], definition["line"], "xacro_macro_definition", definition["snippet"]),
                ],
            }
        )
    return fragments


def scan_ros2_control(text: str, rel_file: str, context: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    model: dict[str, list[dict[str, Any]]] = {
        "hardware_components": [],
        "transmissions": [],
        "command_interfaces": [],
        "state_interfaces": [],
    }
    component_pattern = re.compile(r"<ros2_control\b([^>]*)>(.*?)</ros2_control>", re.DOTALL)
    resource_pattern = re.compile(r"<(joint|sensor|gpio)\b([^>]*)>(.*?)</\1>", re.DOTALL)
    interface_pattern = re.compile(r"<(command_interface|state_interface)\b([^>]*?)(?:/\s*>|>(.*?)</\1>)", re.DOTALL)

    def metadata(offset: int, extractor: str, snippet: str, confidence: float, dynamic: bool) -> dict[str, Any]:
        source_file = context.get("file", rel_file) if context else rel_file
        source_line = line_number(text, offset)
        if context:
            source_line += int(context.get("base_line", 1)) - 1
            extractor = "xacro_macro_expansion"
            confidence = min(confidence, float(context.get("confidence", confidence)))
        if dynamic:
            confidence = min(confidence, 0.72)
        return {
            "file": source_file,
            "line": source_line,
            "confidence": confidence,
            "evidence": [evidence(source_file, source_line, extractor, snippet), *((context or {}).get("extra_evidence") or [])],
        }

    for component_match in component_pattern.finditer(text):
        attributes = xml_attributes(component_match.group(1))
        component_name = attributes.get("name") or "<unnamed ros2_control component>"
        component_type = attributes.get("type") or "system"
        body = component_match.group(2)
        hardware_match = re.search(r"<hardware\b[^>]*>(.*?)</hardware>", body, re.DOTALL)
        plugin_match = re.search(r"<plugin\b[^>]*>(.*?)</plugin>", hardware_match.group(1), re.DOTALL) if hardware_match else None
        plugin = html_lib.unescape(plugin_match.group(1).strip()) if plugin_match else None
        resources = []
        command_identifiers = []
        state_identifiers = []
        for resource_match in resource_pattern.finditer(body):
            resource_type = resource_match.group(1)
            resource_attributes = xml_attributes(resource_match.group(2))
            resource_name = resource_attributes.get("name") or f"<unnamed {resource_type}>"
            resources.append({"name": resource_name, "type": resource_type})
            resource_body = resource_match.group(3)
            for interface_match in interface_pattern.finditer(resource_body):
                interface_kind = interface_match.group(1)
                interface_attributes = xml_attributes(interface_match.group(2))
                interface_name = interface_attributes.get("name") or "<unnamed interface>"
                identifier = f"{resource_name}/{interface_name}"
                interface_offset = component_match.start(2) + resource_match.start(3) + interface_match.start()
                dynamic = any(token in value for value in (component_name, resource_name, interface_name) for token in ("${", "$("))
                source = metadata(interface_offset, "ros2_control_urdf", interface_match.group(0), 0.98, dynamic)
                record = {
                    "identifier": identifier,
                    "name": interface_name,
                    "resource": resource_name,
                    "resource_type": resource_type,
                    "component": component_name,
                    "component_type": component_type,
                    "source": context.get("source", "urdf") if context else "urdf",
                    "parameters": control_parameters(interface_match.group(3) or ""),
                    "file": source["file"],
                    "line": source["line"],
                    "fact_type": "detected",
                    "confidence": source["confidence"],
                    "resolved": not dynamic and (context is None or context.get("resolved", False)),
                    "evidence": source["evidence"],
                }
                model["command_interfaces" if interface_kind == "command_interface" else "state_interfaces"].append(record)
                (command_identifiers if interface_kind == "command_interface" else state_identifiers).append(identifier)
        dynamic = any(token in value for value in (component_name, component_type, plugin or "") for token in ("${", "$("))
        source = metadata(component_match.start(), "ros2_control_urdf", component_match.group(0), 0.98, dynamic)
        model["hardware_components"].append(
            {
                "name": component_name,
                "type": component_type,
                "role": f"ros2_control {component_type} hardware component",
                "source": context.get("source", "urdf") if context else "urdf",
                "plugin": plugin,
                "base_class_type": None,
                "resources": resources,
                "command_interfaces": sorted(set(command_identifiers)),
                "state_interfaces": sorted(set(state_identifiers)),
                "joints": sorted({item["name"] for item in resources if item["type"] == "joint"}),
                "actuators": sorted({item["name"] for item in resources if item["type"] == "actuator"}),
                "file": source["file"],
                "line": source["line"],
                "fact_type": "detected",
                "confidence": source["confidence"],
                "resolved": not dynamic and (context is None or context.get("resolved", False)),
                "evidence": source["evidence"],
            }
        )
    transmission_pattern = re.compile(r"<transmission\b([^>]*)>(.*?)</transmission>", re.DOTALL)
    for match in transmission_pattern.finditer(text):
        attributes = xml_attributes(match.group(1))
        body = match.group(2)
        name = attributes.get("name") or "<unnamed transmission>"
        type_match = re.search(r"<(?:type|plugin)\b[^>]*>(.*?)</(?:type|plugin)>", body, re.DOTALL)
        transmission_type = html_lib.unescape(type_match.group(1).strip()) if type_match else None
        joints = [xml_attributes(item.group(1)).get("name") for item in re.finditer(r"<joint\b([^>]*)>", body)]
        actuators = [xml_attributes(item.group(1)).get("name") for item in re.finditer(r"<actuator\b([^>]*)>", body)]
        dynamic = any(token in str(value or "") for value in (name, transmission_type) for token in ("${", "$("))
        source = metadata(match.start(), "urdf_transmission", match.group(0), 0.96, dynamic)
        model["transmissions"].append(
            {
                "name": name,
                "type": transmission_type,
                "role": "ros2_control transmission mapping",
                "source": context.get("source", "urdf") if context else "urdf",
                "plugin": transmission_type,
                "base_class_type": None,
                "resources": [],
                "command_interfaces": [],
                "state_interfaces": [],
                "joints": sorted({item for item in joints if item}),
                "actuators": sorted({item for item in actuators if item}),
                "file": source["file"],
                "line": source["line"],
                "fact_type": "detected",
                "confidence": source["confidence"],
                "resolved": not dynamic and (context is None or context.get("resolved", False)),
                "evidence": source["evidence"],
            }
        )
    return model


def scan_urdf(path: Path, root: Path, xacro_registry: XacroRegistry | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, list[dict[str, Any]]]]:
    text = read_text(path)
    rel_file = relative(path, root)
    transforms = []
    sensors = []
    frames = set(re.findall(r"<link\b[^>]*\bname\s*=\s*['\"]([^'\"]+)", text))
    joint_pattern = re.compile(r"<joint\b[^>]*\bname\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</joint>", re.DOTALL)
    for match in joint_pattern.finditer(text):
        parent = re.search(r"<parent\b[^>]*\blink\s*=\s*['\"]([^'\"]+)", match.group(2))
        child = re.search(r"<child\b[^>]*\blink\s*=\s*['\"]([^'\"]+)", match.group(2))
        if parent and child:
            transforms.append({"parent": parent.group(1), "child": child.group(1), "joint": match.group(1), "file": rel_file, "line": line_number(text, match.start()), "fact_type": "detected", "confidence": 0.98, "evidence": [evidence(rel_file, line_number(text, match.start()), "urdf_parser", match.group(0))]})
    literal_text = mask_xacro_macro_definitions(text) if path.suffix == ".xacro" else text
    control_spans = [match.span() for match in re.finditer(r"<ros2_control\b[^>]*>.*?</ros2_control>", literal_text, re.DOTALL)]
    for match in re.finditer(r"<sensor\b([^>]*)>", literal_text):
        if any(start <= match.start() < end for start, end in control_spans):
            continue
        attributes = match.group(1)
        name_match = re.search(r"\bname\s*=\s*['\"]([^'\"]+)", attributes)
        type_match = re.search(r"\btype\s*=\s*['\"]([^'\"]+)", attributes)
        if name_match or type_match:
            name = name_match.group(1) if name_match else type_match.group(1)
            dynamic = "${" in name or "$(" in name
            sensors.append(finding("sensor", name, type_match.group(1) if type_match else "urdf sensor", rel_file, line_number(text, match.start()), "urdf_sensor_parser", match.group(0), confidence=0.96 if not dynamic else 0.72, resolved=not dynamic))
    control_model = scan_ros2_control(literal_text, rel_file)
    if path.suffix == ".xacro" and xacro_registry:
        for fragment in expand_xacro_control_fragments(text, rel_file, xacro_registry):
            expanded = scan_ros2_control(fragment["text"], rel_file, fragment)
            for key, entries in expanded.items():
                control_model[key].extend(entries)
    return transforms, sensors, sorted(frames), control_model


def diagnostic_remediation(code: str, evidence_items: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    suggested_files = sorted({str(item.get("file")) for item in evidence_items if item.get("file")})
    remediations = {
        "RD001": (
            "Add or select a ROS 2 package before rescanning.",
            ["Confirm the scan root contains the source workspace.", "Add a valid package.xml to each intended package."],
            ["find . -name package.xml -not -path '*/build/*'", "colcon list"],
            "<package format=\"3\">...</package>",
        ),
        "RD002": (
            "Repair the malformed package manifest.",
            ["Open the reported package.xml at the evidence line.", "Validate the XML and rescan."],
            ["xmllint --noout <path/to/package.xml>"],
            None,
        ),
        "RD003": (
            "Fix the Python syntax error so static extraction can inspect the file.",
            ["Open the reported source line.", "Compile the file and rescan."],
            ["python3 -m py_compile <path/to/source.py>"],
            None,
        ),
        "RD004": (
            "Make the reported file readable or repair the launch syntax.",
            ["Check permissions and the evidence line.", "Validate the file with its native parser, then rescan."],
            ["ls -l <path>", "python3 -m py_compile <path/to/launch.py>"],
            None,
        ),
        "RD005": (
            "Repair invalid XML or explicitly raise the source-file limit for a trusted repository.",
            ["Inspect the reported file and evidence.", "Fix XML syntax or increase max_file_size_bytes deliberately."],
            ["xmllint --noout <path/to/file.xml>", "robot-doctor-scan . --max-file-size-mb <MiB>"],
            None,
        ),
        "RD006": (
            "Give every source package a unique package name.",
            ["Review each reported package.xml.", "Rename or remove the duplicate package and update references."],
            ["colcon list | sort"],
            "Change one <name> value and update dependent package.xml and launch references.",
        ),
        "RD007": (
            "Reduce the scan scope or explicitly raise the file-count limit.",
            ["Exclude generated or vendor trees with COLCON_IGNORE.", "Raise max_files only for a trusted input."],
            ["touch <generated-directory>/COLCON_IGNORE", "robot-doctor-scan . --max-files <count>"],
            None,
        ),
        "RD009": (
            "Reduce source input size or explicitly raise the total-byte limit.",
            ["Exclude generated or vendored content.", "Raise max_total_size_bytes only after reviewing the repository."],
            ["robot-doctor-scan . --max-total-size-mb <MiB>"],
            None,
        ),
        "RD010": (
            "Reduce repository breadth or explicitly raise the traversal limit.",
            ["Mark irrelevant subtrees with COLCON_IGNORE.", "Raise max_repository_entries only for trusted input."],
            ["touch <irrelevant-directory>/COLCON_IGNORE", "robot-doctor-scan . --max-repository-entries <count>"],
            None,
        ),
        "RD101": (
            "Declare the referenced ROS package dependency or suppress the finding if it is intentionally external.",
            ["Confirm the reference is required at build or runtime.", "Add the dependency to package.xml and run rosdep."],
            ["rosdep check --from-paths src --ignore-src"],
            f"<depend>{context.get('dependency', '<dependency>')}</depend>",
        ),
        "RD102": (
            "Add the missing CMake build definition or correct the package build type.",
            ["Create CMakeLists.txt for the ament_cmake package.", "Declare targets, installation, and ament_package()."],
            ["colcon build --packages-select <package>"],
            "cmake_minimum_required(VERSION 3.8)\nproject(<package>)\nfind_package(ament_cmake REQUIRED)\nament_package()",
        ),
        "RD103": (
            "Add Python packaging metadata or correct the package build type.",
            ["Define package metadata in pyproject.toml, setup.cfg, or setup.py.", "Register node executables and rebuild."],
            ["python3 -m build", "colcon build --packages-select <package>"],
            "[project.scripts]\n<node> = \"<module>:main\"",
        ),
        "RD104": (
            "Install the CMake executable so ros2 run and launch can find it.",
            ["Add the target to install(TARGETS ...).", "Rebuild and verify the installed executable."],
            ["colcon build --packages-select <package>", "ros2 pkg executables <package>"],
            "install(TARGETS <target> DESTINATION lib/${PROJECT_NAME})",
        ),
        "RD201": (
            "Make every endpoint on the topic use the same fully qualified message type.",
            ["Inspect publishers and subscribers in the evidence list.", "Align their interface imports and rebuild."],
            ["ros2 topic info <topic> --verbose", "ros2 interface show <interface-type>"],
            None,
        ),
        "RD202": (
            "Launch or implement the missing topic endpoint, or suppress this code when the endpoint is external.",
            ["Confirm the complete launch deployment.", "Add the missing publisher/subscriber or document the external dependency."],
            ["ros2 topic list", "ros2 topic info <topic> --verbose"],
            None,
        ),
        "RD203": (
            "Align publisher and subscriber QoS policies.",
            ["Compare reliability, durability, history, and depth.", "Change one endpoint to a compatible QoS profile."],
            ["ros2 topic info <topic> --verbose"],
            "Use matching reliability and durability settings on both endpoints.",
        ),
        "RD204": (
            "Make every service endpoint use the same fully qualified service type.",
            ["Inspect the reported servers and clients.", "Align their service interface types and rebuild."],
            ["ros2 service type <service>", "ros2 interface show <interface-type>"],
            None,
        ),
        "RD205": (
            "Launch or implement the missing service endpoint, or suppress this code when it is external.",
            ["Confirm the complete deployment.", "Add the missing server/client or document the external dependency."],
            ["ros2 service list -t", "ros2 service type <service>"],
            None,
        ),
        "RD206": (
            "Make every action endpoint use the same fully qualified action type.",
            ["Inspect the reported action servers and clients.", "Align their action interface types and rebuild."],
            ["ros2 action info <action>", "ros2 interface show <interface-type>"],
            None,
        ),
        "RD207": (
            "Launch or implement the missing action endpoint, or suppress this code when it is external.",
            ["Confirm the complete deployment.", "Add the missing server/client or document the external dependency."],
            ["ros2 action list -t", "ros2 action info <action>"],
            None,
        ),
        "RD301": (
            "Correct the launch include path or install the included launch file.",
            ["Resolve substitutions and package-share references.", "Fix the include and verify the target is installed."],
            ["ros2 launch <package> <launch-file> --show-args"],
            "Use get_package_share_directory('<package>') and an existing relative launch path.",
        ),
        "RD302": (
            "Add the referenced parameter file or correct its launch path.",
            ["Locate the intended YAML file.", "Update the launch reference and install the config directory."],
            ["find . -name '*.yaml' -o -name '*.yml'"],
            "install(DIRECTORY config DESTINATION share/${PROJECT_NAME})",
        ),
        "RD303": (
            "Register and install the executable referenced by launch.",
            ["Confirm the executable spelling and package.", "Add it to Python entry points or CMake installation."],
            ["ros2 pkg executables <package>", "colcon build --packages-select <package>"],
            None,
        ),
        "RD401": (
            "Give the TF child frame exactly one parent in each robot model.",
            ["Inspect the reported URDF joints.", "Remove or rename the duplicate parent relationship."],
            ["check_urdf <robot.urdf>", "ros2 run tf2_tools view_frames"],
            None,
        ),
        "RD402": (
            "Break the cycle in the URDF/TF parent-child graph.",
            ["Trace the reported frame chain.", "Remove or redirect one cyclic joint and validate the model."],
            ["check_urdf <robot.urdf>", "ros2 run tf2_tools view_frames"],
            None,
        ),
    }
    summary, steps, commands, patch_hint = remediations.get(
        code,
        (
            "Review the evidence, correct the source configuration, and rescan.",
            ["Open the first evidence location.", "Confirm the intended ROS behavior and rescan."],
            ["robot-doctor-scan . --json --output scan.json"],
            None,
        ),
    )
    interface_name = context.get("interface")
    substitutions = {
        "<topic>": context.get("topic"),
        "<service>": interface_name if code in {"RD204", "RD205"} else None,
        "<action>": interface_name if code in {"RD206", "RD207"} else None,
        "<package>": context.get("package"),
        "<launch-file>": context.get("launch_file"),
        "<path>": suggested_files[0] if suggested_files else None,
    }
    resolved_commands = []
    for command in commands:
        if "<interface-type>" in command:
            interface_types = sorted({str(value) for value in context.get("interface_types", []) if value})
            resolved_commands.extend(command.replace("<interface-type>", shlex.quote(value)) for value in interface_types)
            if interface_types:
                continue
        for placeholder, value in substitutions.items():
            if value:
                command = command.replace(placeholder, shlex.quote(str(value)))
        resolved_commands.append(command)
    return {
        "summary": summary,
        "steps": steps,
        "commands": resolved_commands,
        "suggested_files": suggested_files,
        "patch_hint": patch_hint,
    }


def diagnostic(code: str, severity: str, title: str, message: str, evidence_items: list[dict[str, Any]], confidence: float, **extra: Any) -> dict[str, Any]:
    remediation_context = extra.pop("_remediation_context", {})
    result = {"code": code, "severity": severity, "title": title, "message": message, "fact_type": "diagnostic", "confidence": round(confidence, 2), "evidence": evidence_items}
    result.update(extra)
    result["remediation"] = diagnostic_remediation(code, evidence_items, {**extra, **remediation_context})
    return result


def declared_dependencies(package: dict[str, Any]) -> set[str]:
    return {dependency for values in package["dependencies"].values() for dependency in values}


def is_probable_ros_dependency(name: str, local_names: set[str]) -> bool:
    return name in KNOWN_ROS_IMPORTS or name in local_names or name.endswith(("_msgs", "_interfaces", "_srvs", "_actions", "_description")) or name.startswith(("rcl", "rosidl", "ament_", "tf2"))


COMMUNICATION_KEYS = (
    "publishers",
    "subscriptions",
    "service_servers",
    "service_clients",
    "action_servers",
    "action_clients",
)

DEPLOYMENT_SCOPES = ("production", "test", "example")
TEST_PATH_PARTS = {"test", "tests", "testing", "gtest", "bench", "benchmark", "benchmarks"}
EXAMPLE_PATH_PARTS = {"example", "examples", "demo", "demos", "tutorial", "tutorials"}


def deployment_scope(file: str | None, package_name: str | None = None) -> str:
    parts = [part.casefold() for part in re.split(r"[/\\]+", file or "") if part]
    filename = parts[-1] if parts else ""
    package = (package_name or "").casefold().replace("-", "_")
    if (
        any(part in TEST_PATH_PARTS for part in parts)
        or filename.startswith("test_")
        or re.search(r"(?:^|[_-])(?:test|tests|testing|gtest|bench|benchmark)(?:[_-]|\.)", filename)
        or re.search(r"(?:^|_)test(?:s|ing)?(?:_|$)", package)
        or package.startswith(("test_", "testing_"))
        or package.endswith(("_test", "_tests", "_testing"))
        or "_system_tests" in package
    ):
        return "test"
    if (
        any(part in EXAMPLE_PATH_PARTS for part in parts)
        or re.search(r"(?:^|_)(?:examples?|demos?|tutorials?)(?:_|$)", package)
        or package.startswith(("example_", "examples_", "demo_", "tutorial_"))
        or package.endswith(("_example", "_examples", "_demo", "_demos", "_tutorial", "_tutorials"))
    ):
        return "example"
    return "production"


def combined_deployment_scope(*scopes: str | None) -> str:
    values = {scope for scope in scopes if scope}
    if "test" in values:
        return "test"
    if "example" in values:
        return "example"
    return "production"


def evidence_deployment_scope(evidence_items: Iterable[dict[str, Any]], package_name: str | None = None) -> str:
    scopes = set()
    for item in evidence_items:
        extractor = str(item.get("extractor") or "")
        if extractor.endswith("_test"):
            scopes.add("test")
        elif extractor.endswith("_example"):
            scopes.add("example")
        else:
            scopes.add(deployment_scope(item.get("file"), package_name))
    if "production" in scopes:
        return "production"
    if "example" in scopes:
        return "example"
    return "test" if "test" in scopes else deployment_scope(None, package_name)


def node_class(item: dict[str, Any]) -> str | None:
    return item.get("class") or item.get("class_name")


def executable_matches_node(executable: dict[str, Any], node: dict[str, Any]) -> bool:
    source_file = node.get("source_file") or ""
    target = executable.get("target") or ""
    if target:
        module = target.split(":", 1)[0]
        module_path = module.replace(".", "/") + ".py"
        if source_file.endswith(module_path):
            return True
    return any(source_file.endswith(source) or source_file.endswith("/" + source) for source in executable.get("sources", []))


def source_node_definitions(package_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = []
    for report in package_reports:
        package_name = report["package"]["name"]
        package_nodes = []
        for index, item in enumerate(report["node_names"]):
            class_name = node_class(item)
            source_position = item.get("line") if item.get("line") is not None else index
            node_id = f"source:{package_name}:{class_name or item.get('name') or index}:{item['file']}:{source_position}:{index}"
            package_nodes.append(
                {
                    "id": node_id,
                    "name": item.get("name"),
                    "namespace": "",
                    "package": package_name,
                    "executable": None,
                    "executables": [],
                    "class": class_name,
                    "origin": "source",
                    "deployment_scope": deployment_scope(item["file"], package_name),
                    "launch_condition": None,
                    "definition_id": None,
                    "source_file": item["file"],
                    "line": item.get("line"),
                    "lifecycle": item.get("lifecycle", False),
                    "active": True,
                    "resolved": item.get("resolved", False),
                    "fact_type": "detected",
                    "confidence": item.get("confidence", 0.8),
                    "evidence": item["evidence"],
                    "parameters": [],
                    **{key: [] for key in COMMUNICATION_KEYS},
                }
            )
        synthetic_by_file: dict[str, dict[str, Any]] = {}

        def candidate_node(entity: dict[str, Any]) -> dict[str, Any]:
            class_name = node_class(entity)
            entity_scope = deployment_scope(entity["file"], package_name)
            candidates = [
                item
                for item in package_nodes
                if class_name and item.get("class") == class_name and item.get("deployment_scope") == entity_scope
            ]
            same_file_candidates = [item for item in candidates if item["source_file"] == entity["file"]]
            if same_file_candidates:
                candidates = same_file_candidates
            if not candidates:
                candidates = [item for item in package_nodes if item["source_file"] == entity["file"]]
            if candidates:
                return candidates[0]
            if entity["file"] not in synthetic_by_file:
                synthetic_by_file[entity["file"]] = {
                    "id": f"source:{package_name}:unresolved:{entity['file']}",
                    "name": None,
                    "namespace": "",
                    "package": package_name,
                    "executable": None,
                    "executables": [],
                    "class": class_name,
                    "origin": "source_scope",
                    "deployment_scope": deployment_scope(entity["file"], package_name),
                    "launch_condition": None,
                    "definition_id": None,
                    "source_file": entity["file"],
                    "line": entity.get("line"),
                    "lifecycle": entity.get("lifecycle", False),
                    "active": True,
                    "resolved": False,
                    "fact_type": "inferred",
                    "confidence": 0.58,
                    "evidence": entity["evidence"],
                    "parameters": [],
                    **{key: [] for key in COMMUNICATION_KEYS},
                }
            return synthetic_by_file[entity["file"]]

        for key in COMMUNICATION_KEYS:
            for endpoint in report[key]:
                candidate_node(endpoint)[key].append({"package": package_name, **endpoint})
        for parameter in report["declared_parameters"]:
            node = candidate_node(parameter)
            node["parameters"].append(
                {
                    "name": parameter.get("name"),
                    "value": parameter.get("default"),
                    "type": yaml_value_type(parameter.get("default")),
                    "source": "code_default",
                    "selector": None,
                    "precedence_rank": 10,
                    "effective": True,
                    "confidence": parameter.get("confidence", 0.8),
                    "evidence": parameter["evidence"],
                }
            )
        package_nodes.extend(synthetic_by_file.values())
        for node in package_nodes:
            matching_executables = [item for item in report["executables"] if executable_matches_node(item, node)]
            node["executables"] = sorted({item["name"] for item in matching_executables})
            if len(matching_executables) == 1:
                node["executable"] = matching_executables[0]["name"]
            elif len(report["executables"]) == 1:
                node["executable"] = report["executables"][0]["name"]
        definitions.extend(package_nodes)
    return definitions


def join_ros_namespace(*parts: str | None) -> str:
    values = [str(part).strip("/") for part in parts if part and str(part).strip("/")]
    return "/" + "/".join(values) if values else ""


def effective_ros_name(name: str | None, namespace: str, node_name: str | None, remappings: list[dict[str, Any]]) -> tuple[str | None, bool]:
    if not name:
        return name, False
    original = name
    remap = next((item for item in remappings if (item.get("from") or "").strip("/") == original.strip("/")), None)
    value = remap.get("to") if remap else original
    resolved = not any(token in str(value) for token in ("$(", "${", "{dynamic}"))
    if str(value).startswith("/"):
        return str(value), resolved
    if str(value).startswith("~/"):
        return join_ros_namespace(namespace, node_name, str(value)[2:]), resolved
    return join_ros_namespace(namespace, str(value)) if namespace else str(value), resolved


def selector_matches_node(selector: str | None, node_name: str | None, namespace: str) -> bool:
    if not selector or selector == "/**":
        return True
    fully_qualified = join_ros_namespace(namespace, node_name)
    normalized = "/" + selector.strip("/")
    if normalized.endswith("/**"):
        return fully_qualified.startswith(normalized[:-3])
    return normalized == fully_qualified or selector.strip("/") == (node_name or "").strip("/")


def flatten_inline_parameters(value: Any, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from flatten_inline_parameters(nested, prefix + (str(key),))
    else:
        yield ".".join(prefix), value


def launch_node_parameters(
    definition: dict[str, Any] | None,
    action: dict[str, Any],
    launch_file: str,
    namespace: str,
    root: Path,
    package_reports: list[dict[str, Any]],
    repository_files: Iterable[Path],
) -> list[dict[str, Any]]:
    candidates = [dict(item) for item in (definition or {}).get("parameters", [])]
    packages = [item["package"] for item in package_reports]
    parameter_files = {item["file"]: item for report in package_reports for item in report["parameter_files"]}
    order = 0
    for source in action.get("parameters", []):
        order += 1
        if source.get("kind") == "file":
            resolved_path, exists = resolve_repository_reference(
                str(source.get("value")),
                launch_file,
                root,
                packages,
                repository_files,
            )
            source["resolved_path"] = resolved_path
            source["exists"] = exists
            parameter_file = parameter_files.get(resolved_path or "")
            if parameter_file:
                for override in parameter_file["parameters"]:
                    if selector_matches_node(override.get("selector"), action.get("name") or (definition or {}).get("name"), namespace):
                        candidates.append(
                            {
                                "name": override.get("name"),
                                "value": override.get("value"),
                                "type": override.get("type"),
                                "source": "yaml_override",
                                "selector": override.get("selector"),
                                "precedence_rank": override.get("precedence_rank", 20) + order,
                                "effective": False,
                                "confidence": override.get("confidence", 0.8),
                                "evidence": override["evidence"],
                            }
                        )
        elif source.get("kind") == "inline" and isinstance(source.get("value"), dict):
            for name, value in flatten_inline_parameters(source["value"]):
                candidates.append(
                    {
                        "name": name,
                        "value": value,
                        "type": yaml_value_type(value),
                        "source": "launch_inline",
                        "selector": join_ros_namespace(namespace, action.get("name")),
                        "precedence_rank": 100 + order,
                        "effective": False,
                        "confidence": source.get("confidence", 0.85),
                        "evidence": action["evidence"],
                    }
                )
    winners: dict[str, dict[str, Any]] = {}
    for item in candidates:
        name = item.get("name")
        if name is not None and (name not in winners or item["precedence_rank"] >= winners[name]["precedence_rank"]):
            winners[name] = item
    for item in candidates:
        item["effective"] = winners.get(item.get("name")) is item
    return candidates


def build_node_architecture(
    package_reports: list[dict[str, Any]],
    launch_files: list[dict[str, Any]],
    root: Path,
    repository_files: Iterable[Path],
) -> list[dict[str, Any]]:
    definitions = source_node_definitions(package_reports)
    instances = []
    matched_definitions = set()
    for launch in launch_files:
        namespace_actions = [item.get("value") for item in launch["actions"] if item["kind"] == "namespace" and item.get("resolved")]
        inherited_namespace = namespace_actions[0] if len(namespace_actions) == 1 else ""
        for index, action in enumerate(launch["actions"]):
            if action["kind"] not in {"node", "composable_node", "container"}:
                continue
            candidates = [item for item in definitions if item["package"] == action.get("package")]
            exact = [item for item in candidates if action.get("executable") and action.get("executable") in item.get("executables", [])]
            if action["kind"] == "composable_node" and action.get("executable"):
                plugin_class = action["executable"].split("::")[-1]
                exact.extend(item for item in candidates if item.get("class") == plugin_class)
            functional_exact = [item for item in exact if any(item[key] for key in COMMUNICATION_KEYS)]
            definition = functional_exact[0] if len(functional_exact) == 1 else exact[0] if len(exact) == 1 else candidates[0] if len(candidates) == 1 else None
            if definition:
                matched_definitions.add(definition["id"])
            namespace = join_ros_namespace(inherited_namespace, action.get("namespace"))
            node_name = action.get("name") or (definition or {}).get("name") or action.get("executable")
            node_id = f"launch:{launch['file']}:{index}:{namespace}:{node_name}"
            instance = {
                "id": node_id,
                "name": node_name,
                "namespace": namespace,
                "package": action.get("package"),
                "executable": action.get("executable"),
                "executables": [action.get("executable")] if action.get("executable") else [],
                "class": (definition or {}).get("class"),
                "origin": "launch",
                "deployment_scope": combined_deployment_scope(
                    deployment_scope(launch["file"], launch.get("package")),
                    (definition or {}).get("deployment_scope"),
                ),
                "launch_condition": action.get("condition"),
                "definition_id": (definition or {}).get("id"),
                "source_file": launch["file"],
                "line": action.get("line"),
                "lifecycle": action.get("lifecycle", False) or (definition or {}).get("lifecycle", False),
                "active": True,
                "resolved": action.get("resolved", False),
                "fact_type": "detected",
                "confidence": min(action.get("confidence", 0.8), (definition or {}).get("confidence", 1.0)),
                "evidence": action["evidence"] + (definition or {}).get("evidence", []),
                "parameters": launch_node_parameters(
                    definition,
                    action,
                    launch["file"],
                    namespace,
                    root,
                    package_reports,
                    repository_files,
                ),
                **{key: [] for key in COMMUNICATION_KEYS},
            }
            for key in COMMUNICATION_KEYS:
                for endpoint in (definition or {}).get(key, []):
                    effective_name, resolved = effective_ros_name(endpoint.get("name"), namespace, node_name, action.get("remappings", []))
                    instance[key].append(
                        {
                            **endpoint,
                            "original_name": endpoint.get("name"),
                            "name": effective_name,
                            "resolved": endpoint.get("resolved", False) and resolved,
                            "node_id": node_id,
                            "node_name": node_name,
                        }
                    )
            instances.append(instance)
    for definition in definitions:
        definition["active"] = definition["id"] not in matched_definitions
        for key in COMMUNICATION_KEYS:
            definition[key] = [{**endpoint, "node_id": definition["id"], "node_name": definition.get("name")} for endpoint in definition[key]]
    return definitions + instances


def communication_architecture(nodes: list[dict[str, Any]], left_key: str, right_key: str, left_label: str, right_label: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {left_label: [], right_label: []})
    scopes_by_node_id = {node["id"]: node.get("deployment_scope", "production") for node in nodes}
    for node in nodes:
        if not node.get("active"):
            continue
        for label, key in ((left_label, left_key), (right_label, right_key)):
            for endpoint in node[key]:
                if endpoint.get("resolved") and endpoint.get("name"):
                    grouped[endpoint["name"]][label].append(endpoint)
    results = []
    for name, endpoints in sorted(grouped.items()):
        types = sorted({item["type"] for values in endpoints.values() for item in values if item.get("type")})
        evidence_items = [item["evidence"][0] for values in endpoints.values() for item in values]
        node_scopes = {scopes_by_node_id.get(endpoint.get("node_id"), "production") for values in endpoints.values() for endpoint in values}
        results.append(
            {
                "name": name,
                "types": types,
                "deployment_scopes": sorted(node_scopes),
                **endpoints,
                "fact_type": "detected",
                "confidence": min((item["confidence"] for values in endpoints.values() for item in values), default=0.0),
                "evidence": evidence_items,
            }
        )
    return results


def control_entity_from_plugin(plugin: dict[str, Any], package_name: str) -> dict[str, Any]:
    return {
        "name": plugin.get("name") or "<unnamed plugin>",
        "type": plugin.get("type"),
        "role": plugin.get("role") or "ros2_control plugin",
        "source": "pluginlib",
        "plugin": plugin.get("name"),
        "base_class_type": plugin.get("base_class_type"),
        "resources": [],
        "command_interfaces": [],
        "state_interfaces": [],
        "joints": [],
        "actuators": [],
        "package": package_name,
        "deployment_scope": deployment_scope(plugin.get("file"), package_name),
        "file": plugin.get("file") or "",
        "line": plugin.get("line"),
        "fact_type": "detected",
        "confidence": plugin.get("confidence", 0.0),
        "resolved": plugin.get("resolved", False),
        "evidence": plugin.get("evidence") or [],
    }


def control_chain_evidence(*items: dict[str, Any] | None) -> list[dict[str, Any]]:
    results = []
    seen = set()
    for item in items:
        for record in (item or {}).get("evidence", []):
            key = (record.get("file"), record.get("line"), record.get("extractor"), record.get("snippet"))
            if key not in seen:
                seen.add(key)
                results.append(record)
    return results


def controller_interface_identifiers(controller: dict[str, Any]) -> set[str]:
    commands = {str(value) for value in controller.get("command_interfaces") or []}
    joints = {str(value) for value in controller.get("joints") or []}
    return {
        command if "/" in command else f"{joint}/{command}"
        for command in commands
        for joint in joints or {""}
        if command and (joint or "/" in command)
    }


def ranked_control_interfaces(controller: dict[str, Any], interfaces: list[dict[str, Any]], identifier: str) -> list[dict[str, Any]]:
    candidates = [item for item in interfaces if item.get("identifier") == identifier]
    controller_scope = controller.get("deployment_scope") or "production"
    same_scope = [item for item in candidates if item.get("deployment_scope") == controller_scope]
    if same_scope:
        candidates = same_scope
    elif controller_scope in {"test", "example"}:
        candidates = [item for item in candidates if item.get("deployment_scope") == "production"]
    else:
        candidates = []
    same_package = [item for item in candidates if item.get("package") == controller.get("package")]
    return same_package or candidates


def control_interface_identity(interface: dict[str, Any]) -> tuple[Any, ...]:
    return (
        interface.get("package"),
        interface.get("file"),
        interface.get("line"),
        interface.get("component"),
        interface.get("identifier"),
    )


def control_interface_label(interface: dict[str, Any]) -> str:
    location = f"{interface.get('file') or '<unknown>'}:{interface.get('line') or '?'}"
    return f"{interface.get('package') or '<unknown>'}:{interface.get('component') or '<unresolved>'}:{interface.get('identifier') or '<unresolved>'} ({location})"


def build_control_chains(model: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    configured_controllers = [item for item in model["controllers"] if item.get("source") == "parameter"]
    transmissions = [item for item in model["transmissions"] if item.get("source") in {"urdf", "xacro"}]
    chains = []
    matched_interfaces: set[tuple[Any, ...]] = set()

    def component_for(interface: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in model["hardware_components"]
            if item.get("package") == interface.get("package") and item.get("name") == interface.get("component")
        ]
        same_file = [item for item in candidates if item.get("file") == interface.get("file")]
        candidates = same_file or candidates
        return candidates[0] if len(candidates) == 1 else None

    def transmission_for(interface: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in transmissions
            if item.get("package") == interface.get("package") and interface.get("resource") in item.get("joints", [])
        ]
        same_scope = [item for item in candidates if item.get("deployment_scope") == interface.get("deployment_scope")]
        candidates = same_scope or candidates
        return candidates[0] if len(candidates) == 1 else None

    def append_linked_chain(controller: dict[str, Any] | None, interface: dict[str, Any], status: str, basis: str) -> None:
        component = component_for(interface)
        transmission = transmission_for(interface)
        scopes = [
            interface.get("deployment_scope"),
            (component or {}).get("deployment_scope"),
            (controller or {}).get("deployment_scope"),
            (transmission or {}).get("deployment_scope"),
        ]
        resolved = bool(
            status == "unique_match"
            and controller
            and component
            and controller.get("resolved")
            and component.get("resolved")
            and interface.get("resolved")
        )
        confidence = min(
            [float(item.get("confidence", 0.0)) for item in (controller, interface, component, transmission) if item],
            default=0.0,
        )
        confidence = min(confidence, 0.92 if status == "unique_match" else 0.65)
        chain_id = ":".join(
            str(value or "unresolved")
            for value in (
                (controller or {}).get("package"),
                (controller or {}).get("name"),
                interface.get("package"),
                interface.get("identifier"),
                interface.get("file"),
                interface.get("line"),
                (component or {}).get("name"),
                (transmission or {}).get("name"),
            )
        )
        chains.append(
            {
                "id": chain_id,
                "controller": (controller or {}).get("name"),
                "controller_type": (controller or {}).get("type"),
                "command_interface": interface.get("identifier"),
                "interface_name": interface.get("name"),
                "hardware_component": (component or {}).get("name"),
                "hardware_plugin": (component or {}).get("plugin"),
                "resource": interface.get("resource"),
                "resource_type": interface.get("resource_type"),
                "transmission": (transmission or {}).get("name"),
                "actuators": list((transmission or {}).get("actuators") or []),
                "package": (controller or interface).get("package") or "",
                "deployment_scope": combined_deployment_scope(*scopes),
                "match_status": status,
                "match_basis": basis,
                "candidate_hardware_components": [control_interface_label(interface)],
                "fact_type": "inferred",
                "confidence": round(confidence, 2),
                "resolved": resolved,
                "evidence": control_chain_evidence(controller, interface, component, transmission),
            }
        )

    def append_candidate_chain(
        controller: dict[str, Any],
        identifier: str,
        candidates: list[dict[str, Any]],
        status: str,
        basis: str,
        confidence_cap: float,
    ) -> None:
        joint, _, interface_name = identifier.partition("/")
        chains.append(
            {
                "id": f"{controller['package']}:{controller['name']}:{identifier}:{status}",
                "controller": controller["name"],
                "controller_type": controller.get("type"),
                "command_interface": identifier,
                "interface_name": interface_name or None,
                "hardware_component": None,
                "hardware_plugin": None,
                "resource": joint or None,
                "resource_type": "joint" if joint else None,
                "transmission": None,
                "actuators": [],
                "package": controller["package"],
                "deployment_scope": combined_deployment_scope(
                    controller.get("deployment_scope"),
                    *(item.get("deployment_scope") for item in candidates),
                ),
                "match_status": status,
                "match_basis": basis,
                "candidate_hardware_components": sorted({control_interface_label(item) for item in candidates}),
                "fact_type": "inferred",
                "confidence": round(min(float(controller.get("confidence", 0.0)), confidence_cap), 2),
                "resolved": False,
                "evidence": control_chain_evidence(controller, *candidates),
            }
        )

    for controller in configured_controllers:
        for identifier in sorted(controller_interface_identifiers(controller)):
            candidates = ranked_control_interfaces(controller, model["command_interfaces"], identifier)
            matched_interfaces.update(control_interface_identity(item) for item in candidates)
            if len(candidates) == 1 and candidates[0].get("package") == controller.get("package"):
                append_linked_chain(
                    controller,
                    candidates[0],
                    "unique_match",
                    "unique deployment-scope and package-qualified controller/interface match",
                )
                continue
            if len(candidates) == 1:
                append_candidate_chain(
                    controller,
                    identifier,
                    candidates,
                    "cross_package_candidate",
                    "one scope-compatible interface exists in another package, but no static deployment evidence proves the controller-to-hardware link",
                    0.55,
                )
                continue
            joint, _, interface_name = identifier.partition("/")
            if len(candidates) > 1:
                append_candidate_chain(
                    controller,
                    identifier,
                    candidates,
                    "ambiguous",
                    "multiple equally ranked package/scope-compatible hardware interfaces",
                    0.58,
                )
                continue
            chains.append(
                {
                    "id": f"{controller['package']}:{controller['name']}:{identifier}:unresolved:unresolved",
                    "controller": controller["name"],
                    "controller_type": controller.get("type"),
                    "command_interface": identifier,
                    "interface_name": interface_name or None,
                    "hardware_component": None,
                    "hardware_plugin": None,
                    "resource": joint or None,
                    "resource_type": "joint" if joint else None,
                    "transmission": None,
                    "actuators": [],
                    "package": controller["package"],
                    "deployment_scope": controller["deployment_scope"],
                    "match_status": "missing_interface",
                    "match_basis": "no deployment-scope-compatible command interface matched the controller claim",
                    "candidate_hardware_components": [],
                    "fact_type": "inferred",
                    "confidence": round(min(float(controller.get("confidence", 0.0)), 0.62), 2),
                    "resolved": False,
                    "evidence": controller["evidence"],
                }
            )
    for interface in model["command_interfaces"]:
        if control_interface_identity(interface) not in matched_interfaces:
            append_linked_chain(
                None,
                interface,
                "unclaimed",
                "command interface has no matching configured controller claim",
            )
    scope_rank = {"production": 0, "example": 1, "test": 2}
    unique = {item["id"]: item for item in chains}
    status_rank = {"unique_match": 0, "cross_package_candidate": 1, "ambiguous": 2, "missing_interface": 3, "unclaimed": 4}
    return sorted(unique.values(), key=lambda item: (scope_rank.get(item["deployment_scope"], 3), status_rank.get(item["match_status"], 4), item.get("package") or "", item.get("controller") or "", item.get("command_interface") or ""))


def build_ros2_control_model(package_reports: list[dict[str, Any]], urdf_control: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    model = {key: list(values) for key, values in urdf_control.items()}
    model.update({"controllers": [], "plugins": []})
    for report in package_reports:
        package_name = report["package"]["name"]
        for plugin in report["plugins"]:
            category = plugin.get("plugin_category")
            if category not in {"hardware_actuator", "hardware_sensor", "hardware_system", "controller", "transmission"}:
                continue
            entity = control_entity_from_plugin(plugin, package_name)
            model["plugins"].append(entity)
            if category == "controller":
                model["controllers"].append(entity)
            elif category == "transmission":
                model["transmissions"].append(entity)
        for parameter in report["parameter_overrides"]:
            selector = str(parameter.get("selector") or "").casefold()
            parameter_path = parameter.get("parameter_path") or []
            controller_type = parameter.get("value")
            if "controller_manager" not in selector or len(parameter_path) < 2 or parameter_path[-1] != "type" or not isinstance(controller_type, str):
                continue
            controller_name = ".".join(str(part) for part in parameter_path[:-1])
            controller_configuration: dict[str, list[str]] = {"joints": [], "command_interfaces": [], "state_interfaces": []}
            for candidate in report["parameter_overrides"]:
                candidate_path = [str(part) for part in candidate.get("parameter_path") or []]
                candidate_selector = str(candidate.get("selector") or "").strip("/").split("/")[-1]
                field = None
                if candidate_selector == controller_name and candidate_path:
                    field = candidate_path[-1]
                elif len(candidate_path) >= 2 and ".".join(candidate_path[:-1]) == controller_name:
                    field = candidate_path[-1]
                if field not in controller_configuration:
                    continue
                value = candidate.get("value")
                if isinstance(value, list):
                    controller_configuration[field].extend(str(item) for item in value)
                elif value is not None:
                    controller_configuration[field].append(str(value))
            model["controllers"].append(
                {
                    "name": controller_name,
                    "type": controller_type,
                    "role": "configured ros2_control controller",
                    "source": "parameter",
                    "plugin": controller_type,
                    "base_class_type": None,
                    "resources": [],
                    "command_interfaces": sorted(set(controller_configuration["command_interfaces"])),
                    "state_interfaces": sorted(set(controller_configuration["state_interfaces"])),
                    "joints": sorted(set(controller_configuration["joints"])),
                    "actuators": [],
                    "package": package_name,
                    "deployment_scope": deployment_scope(parameter.get("file"), package_name),
                    "file": parameter.get("file") or "",
                    "line": parameter.get("line"),
                    "fact_type": "detected",
                    "confidence": min(parameter.get("confidence", 0.0), 0.94),
                    "resolved": parameter.get("resolved", False),
                    "evidence": parameter.get("evidence") or [],
                }
            )
    keys = ("hardware_components", "controllers", "transmissions", "command_interfaces", "state_interfaces", "plugins")
    for key in keys:
        model[key] = sorted(
            unique_findings(model.get(key, [])),
            key=lambda item: (DEPLOYMENT_SCOPES.index(item.get("deployment_scope")) if item.get("deployment_scope") in DEPLOYMENT_SCOPES else 3, item.get("package") or "", item.get("file") or "", item.get("line") or 0, item.get("name") or ""),
        )
    model["control_chains"] = build_control_chains(model)
    return model


def inferred_control_finding(item: dict[str, Any], kind: str, name: str, type_name: str | None, role: str, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "type": type_name,
        "file": item.get("file") or "",
        "line": item.get("line"),
        "fact_type": "inferred",
        "confidence": round(min(float(item.get("confidence", 0.0)), confidence), 2),
        "resolved": item.get("resolved", False),
        "evidence": item.get("evidence") or [],
        "package": item.get("package") or "",
        "deployment_scope": item.get("deployment_scope") or "production",
        "role": role,
    }


def infer_architecture(package_reports: list[dict[str, Any]], urdf_sensors: list[dict[str, Any]], ros2_control: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    sensors = [{**item, "deployment_scope": item.get("deployment_scope") or deployment_scope(item.get("file"), item.get("package"))} for item in urdf_sensors]
    actuators = []
    algorithms = []
    sensor_types = ("laserscan", "image", "camerainfo", "pointcloud", "imu", "navsat", "range", "fluidpressure", "magneticfield", "batterystate", "jointstate")
    actuator_types = ("twist", "jointtrajectory", "ackermanndrive", "followjointtrajectory", "grippercommand")
    for report in package_reports:
        package_name = report["package"]["name"]
        for endpoint in report["publishers"] + report["subscriptions"]:
            combined = f"{endpoint.get('name') or ''} {endpoint.get('type') or ''}".lower()
            if any(token in combined for token in sensor_types):
                sensors.append({**endpoint, "package": package_name, "deployment_scope": deployment_scope(endpoint.get("file"), package_name), "role": "sensor data interface", "fact_type": "inferred", "confidence": round(min(endpoint["confidence"], 0.78), 2)})
        for endpoint in report["publishers"] + report["action_clients"] + report["service_clients"]:
            combined = f"{endpoint.get('name') or ''} {endpoint.get('type') or ''}".lower()
            if any(token in combined for token in actuator_types) or any(token in combined for token in ("cmd_vel", "command", "motor", "gripper", "actuat")):
                actuators.append({**endpoint, "package": package_name, "deployment_scope": deployment_scope(endpoint.get("file"), package_name), "role": "command or actuation interface", "fact_type": "inferred", "confidence": round(min(endpoint["confidence"], 0.75), 2)})
        for plugin in report["plugins"]:
            item = {**plugin, "package": package_name, "deployment_scope": deployment_scope(plugin.get("file"), package_name), "fact_type": "inferred"}
            category = plugin.get("plugin_category")
            if category == "hardware_sensor":
                sensors.append({**item, "role": plugin.get("role") or "ros2_control sensor hardware", "confidence": round(min(plugin["confidence"], 0.94), 2)})
            elif category in {"hardware_actuator", "hardware_system"}:
                actuators.append({**item, "role": plugin.get("role") or "ros2_control hardware", "confidence": round(min(plugin["confidence"], 0.94), 2)})
            elif category == "transmission":
                actuators.append({**item, "role": plugin.get("role") or "ros2_control transmission", "confidence": round(min(plugin["confidence"], 0.92), 2)})
            else:
                algorithms.append({**item, "role": plugin.get("role") or infer_algorithm_role(plugin.get("name") or ""), "confidence": round(min(plugin["confidence"], 0.9 if category == "controller" else 0.78), 2)})
    for component in ros2_control["hardware_components"]:
        component_name = str(component.get("name") or "<unnamed component>")
        component_type = str(component.get("type") or "system")
        if component_type.casefold() == "sensor":
            sensors.append(inferred_control_finding(component, "sensor", component_name, component.get("plugin") or component_type, "ros2_control sensor component", 0.96))
        elif component.get("command_interfaces") or component_type.casefold() in {"actuator", "system"}:
            actuators.append(inferred_control_finding(component, "actuation", component_name, component.get("plugin") or component_type, "ros2_control hardware component", 0.96))
    for interface in ros2_control["command_interfaces"]:
        actuators.append(inferred_control_finding(interface, "actuation", interface["identifier"], interface.get("name"), "ros2_control command interface", 0.98))
    scope_rank = {"production": 0, "example": 1, "test": 2}
    ordered = lambda items: sorted(unique_findings(items), key=lambda item: (scope_rank.get(item.get("deployment_scope"), 3), item.get("package") or "", item.get("file") or "", item.get("name") or ""))
    return {"sensors": ordered(sensors), "actuation": ordered(actuators), "algorithms": ordered(algorithms)}


def modification_points(package_reports: list[dict[str, Any]], ros2_control: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    results = []
    scope_rank = {"production": 0, "example": 1, "test": 2}

    def preferred(items: Iterable[Any], package_name: str, file_getter: Callable[[Any], str]) -> tuple[Any, str] | tuple[None, None]:
        candidates = [(item, deployment_scope(file_getter(item), package_name)) for item in items]
        return min(candidates, key=lambda value: scope_rank.get(value[1], 3)) if candidates else (None, None)

    for report in package_reports:
        package = report["package"]["name"]
        launch_file, launch_scope = preferred(report["launch_files"], package, lambda item: item["file"])
        if launch_file:
            results.append({"task": "Change startup, composition, namespaces, or remappings", "path": launch_file["file"], "package": package, "deployment_scope": launch_scope, "reason": "launch entry point detected", "fact_type": "inferred", "confidence": 0.85, "evidence": launch_file["evidence"]})
        parameter_file, parameter_scope = preferred(report["parameter_files"], package, lambda item: item["file"])
        if parameter_file:
            results.append({"task": "Tune runtime behavior and algorithm settings", "path": parameter_file["file"], "package": package, "deployment_scope": parameter_scope, "reason": "ROS parameter file detected", "fact_type": "inferred", "confidence": 0.82, "evidence": parameter_file["evidence"]})
        urdf_file, urdf_scope = preferred(report["urdf_files"], package, str)
        if urdf_file:
            results.append({"task": "Change robot geometry, joints, sensors, or frame structure", "path": urdf_file, "package": package, "deployment_scope": urdf_scope, "reason": "URDF/Xacro model detected", "fact_type": "inferred", "confidence": 0.9, "evidence": [evidence(urdf_file, 1, "path_classifier", "URDF/Xacro")]})
        interface, interface_scope = preferred(report["interfaces"], package, lambda item: item["file"])
        if interface:
            results.append({"task": "Change a ROS message, service, or action contract", "path": interface["file"], "package": package, "deployment_scope": interface_scope, "reason": "custom interface detected", "fact_type": "inferred", "confidence": 0.9, "evidence": interface["evidence"]})
        entities = [item for key in ("publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients") for item in report[key]]
        if entities:
            entity_scopes = {item["file"]: deployment_scope(item["file"], package) for item in entities}
            best_scope = min(entity_scopes.values(), key=lambda scope: scope_rank.get(scope, 3))
            scoped_entities = [item for item in entities if entity_scopes[item["file"]] == best_scope]
            source = max({item["file"] for item in scoped_entities}, key=lambda file: sum(item["file"] == file for item in scoped_entities))
            source_entity = next(item for item in scoped_entities if item["file"] == source)
            results.append({"task": "Change node behavior or ROS communication", "path": source, "package": package, "deployment_scope": best_scope, "reason": "source file contains ROS entities", "fact_type": "inferred", "confidence": 0.8, "evidence": source_entity["evidence"]})
    control_guidance = (
        ("hardware_components", "Change ros2_control hardware components and plugin wiring", "ros2_control hardware declaration detected"),
        ("command_interfaces", "Change ros2_control command and state interfaces", "ros2_control command interface detected"),
        ("state_interfaces", "Change ros2_control command and state interfaces", "ros2_control state interface detected"),
        ("controllers", "Configure or implement ros2_control controllers", "ros2_control controller declaration detected"),
        ("transmissions", "Change ros2_control transmission mappings or loaders", "ros2_control transmission declaration detected"),
        ("plugins", "Implement or register ros2_control hardware and control plugins", "typed ros2_control plugin declaration detected"),
    )
    seen_control_points = set()
    for key, task, reason in control_guidance:
        for item in ros2_control[key]:
            if key == "plugins" and "hardware_interface::" not in str(item.get("base_class_type") or ""):
                continue
            identity = (task, item.get("package"), item.get("file"), item.get("deployment_scope"))
            if identity in seen_control_points:
                continue
            seen_control_points.add(identity)
            results.append(
                {
                    "task": task,
                    "path": item.get("file") or "",
                    "package": item.get("package") or "",
                    "deployment_scope": item.get("deployment_scope") or "production",
                    "reason": reason,
                    "fact_type": "inferred",
                    "confidence": round(min(float(item.get("confidence", 0.0)), 0.94), 2),
                    "evidence": item.get("evidence") or [],
                }
            )
    return sorted(results, key=lambda item: (scope_rank.get(item["deployment_scope"], 3), item["package"], item["path"], item["task"]))


def run_diagnostics(
    data: dict[str, Any],
    uninstalled_targets: dict[str, set[str]],
    initial: list[dict[str, Any]],
    repository_files: Iterable[Path],
) -> list[dict[str, Any]]:
    diagnostics = list(initial)
    session = _ACTIVE_SCAN_SESSION.get()
    config = session.config if session else ScanConfig()
    reports = data["packages"]
    if not reports:
        diagnostics.append(diagnostic("RD001", "warning", "No ROS packages detected", "No usable package.xml was found outside ignored directories.", [], 1.0))
        return diagnostics
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        by_name[report["package"]["name"]].append(report)
    for name, duplicates in by_name.items():
        if len(duplicates) > 1:
            diagnostics.append(diagnostic("RD006", "error", "Duplicate package name", f"Package name '{name}' occurs in {len(duplicates)} source locations.", [item["package"]["evidence"][0] for item in duplicates], 1.0))
    local_names = set(by_name)
    for report in reports:
        package = report["package"]
        dependencies = declared_dependencies(package)
        missing = sorted(name for name in report["referenced_packages"] if name != package["name"] and is_probable_ros_dependency(name, local_names) and name not in dependencies)
        for name in missing:
            if config.dependency_mode == "off" or config.ignores_dependency(package["name"], name):
                continue
            dependency_evidence = report["reference_evidence"].get(name, package["evidence"])
            extractors = {item.get("extractor") for item in dependency_evidence}
            scope = evidence_deployment_scope(dependency_evidence, package["name"])
            if scope == "test":
                continue
            direct_reference = any(str(extractor).startswith("cmake_find_package") or "launch" in str(extractor) for extractor in extractors)
            warning = config.dependency_mode == "all" or direct_reference
            diagnostics.append(
                diagnostic(
                    "RD101",
                    "warning" if warning else "info",
                    "Likely missing package dependency" if warning else "Possible undeclared dependency",
                    f"{package['name']} references '{name}' but package.xml does not declare it."
                    + ("" if warning else " This reference is indirect and may be a namespace, test-only import, or transitive dependency."),
                    dependency_evidence,
                    0.82 if warning else 0.58,
                    package=package["name"],
                    dependency=name,
                    direct_reference=direct_reference,
                    deployment_scope=scope,
                )
            )
        package_path = Path(data["repository"]["path"]) / package["path"]
        if package.get("build_type") == "ament_cmake" and not (package_path / "CMakeLists.txt").exists():
            diagnostics.append(diagnostic("RD102", "error", "Missing CMakeLists.txt", f"ament_cmake package {package['name']} has no CMakeLists.txt.", package["evidence"], 1.0, package=package["name"]))
        if package.get("build_type") == "ament_python" and not any((package_path / candidate).exists() for candidate in ("setup.py", "setup.cfg", "pyproject.toml")):
            diagnostics.append(diagnostic("RD103", "error", "Missing Python package definition", f"ament_python package {package['name']} has no setup.py, setup.cfg, or pyproject.toml.", package["evidence"], 1.0, package=package["name"]))
        for target in sorted(uninstalled_targets.get(package["path"], set())):
            executable = next((item for item in report["executables"] if item["name"] == target), None)
            scope = (executable or {}).get("deployment_scope") or deployment_scope(target, package["name"])
            if scope == "test":
                continue
            diagnostics.append(diagnostic("RD104", "warning", "CMake executable may not be installed", f"Target '{target}' is created but was not found in install(TARGETS ...).", executable["evidence"] if executable else package["evidence"], 0.9, package=package["name"], deployment_scope=scope))
    nodes_by_id = {node["id"]: node for node in data["architecture"]["nodes"]}

    def endpoint_scope(endpoint: dict[str, Any]) -> str:
        return nodes_by_id.get(endpoint.get("node_id"), {}).get("deployment_scope", "production")

    def diagnostic_endpoint_groups(endpoint_groups: list[list[dict[str, Any]]]) -> tuple[list[list[dict[str, Any]]], str | None]:
        production = [[endpoint for endpoint in group if endpoint_scope(endpoint) == "production"] for group in endpoint_groups]
        if any(production):
            return production, "production"
        examples = [[endpoint for endpoint in group if endpoint_scope(endpoint) == "example"] for group in endpoint_groups]
        if any(examples):
            return examples, "example"
        return [[] for _ in endpoint_groups], None

    def endpoint_types(endpoint_groups: list[list[dict[str, Any]]]) -> list[str]:
        return sorted({endpoint["type"] for group in endpoint_groups for endpoint in group if endpoint.get("type")})

    def endpoint_evidence(endpoint_groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [item for group in endpoint_groups for endpoint in group for item in endpoint.get("evidence", [])]

    def has_type_mismatch(types: list[str]) -> bool:
        qualified_types = {type_name for type_name in types if "/" in type_name}
        type_basenames = {re.split(r"[/.:]", type_name)[-1] for type_name in types}
        return len(qualified_types) > 1 or (not qualified_types and len(type_basenames) > 1)

    def endpoints_share_deployment(endpoint_groups: list[list[dict[str, Any]]], scope: str | None) -> bool:
        if scope != "production":
            return False
        types_by_launch_node: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for endpoint in (endpoint for group in endpoint_groups for endpoint in group):
            node_id = endpoint.get("node_id")
            type_name = endpoint.get("type")
            node = nodes_by_id.get(node_id, {})
            if (
                not node_id
                or not type_name
                or node.get("origin") != "launch"
                or not node.get("resolved")
                or node.get("deployment_scope") != "production"
                or node.get("launch_condition")
                or not node.get("source_file")
            ):
                continue
            types_by_launch_node[node["source_file"]][node_id].add(type_name)
        return any(
            len(types_by_node) > 1 and len({type_name for types in types_by_node.values() for type_name in types}) > 1
            for types_by_node in types_by_launch_node.values()
        )

    def endpoints_share_launch(endpoint_pair: list[dict[str, Any]]) -> bool:
        launch_nodes: dict[str, set[str]] = defaultdict(set)
        for endpoint in endpoint_pair:
            node = nodes_by_id.get(endpoint.get("node_id"), {})
            if (
                node.get("origin") == "launch"
                and node.get("resolved")
                and node.get("deployment_scope") == "production"
                and not node.get("launch_condition")
                and node.get("source_file")
            ):
                launch_nodes[node["source_file"]].add(node["id"])
        return any(len(node_ids) > 1 for node_ids in launch_nodes.values())

    for topic in data["architecture"]["topics"]:
        endpoint_groups, scope = diagnostic_endpoint_groups([topic["publishers"], topic["subscribers"]])
        if scope is None:
            continue
        publishers, subscribers = endpoint_groups
        types = endpoint_types(endpoint_groups)
        selected_evidence = endpoint_evidence(endpoint_groups)
        if has_type_mismatch(types):
            deployed_together = endpoints_share_deployment(endpoint_groups, scope)
            diagnostics.append(diagnostic("RD201", "error" if deployed_together else "warning", "Topic type mismatch", f"Topic '{topic['name']}' uses multiple static types in {scope} scope: {', '.join(types)}." + ("" if deployed_together else " The endpoints are not proven to run together."), selected_evidence, 0.96 if deployed_together else 0.72, topic=topic["name"], deployment_scope=scope, _remediation_context={"interface_types": types}))
        if not publishers or not subscribers:
            missing_side = "publisher" if not publishers else "subscriber"
            diagnostics.append(diagnostic("RD202", "info", "Orphan topic endpoint", f"Topic '{topic['name']}' has no statically detected {scope} {missing_side}. Runtime or external nodes may provide it.", selected_evidence, 0.62, topic=topic["name"], deployment_scope=scope))
        for publisher in publishers:
            for subscriber in subscribers:
                pub_qos = publisher.get("qos") or {}
                sub_qos = subscriber.get("qos") or {}
                incompatible = pub_qos.get("reliability") == "best_effort" and sub_qos.get("reliability") == "reliable"
                incompatible = incompatible or (pub_qos.get("durability") == "volatile" and sub_qos.get("durability") == "transient_local")
                if incompatible:
                    deployed_together = endpoints_share_launch([publisher, subscriber])
                    diagnostics.append(diagnostic("RD203", "error" if deployed_together else "warning", "Potential QoS incompatibility", f"Publisher and subscriber on '{topic['name']}' request incompatible QoS policies." + ("" if deployed_together else " The endpoints are not proven to run together."), publisher["evidence"] + subscriber["evidence"], 0.9 if deployed_together else 0.7, topic=topic["name"], deployment_scope=scope))
    graph_checks = [
        ("services", "servers", "clients", "service", "RD204", "RD205"),
        ("actions", "servers", "clients", "action", "RD206", "RD207"),
    ]
    for graph_name, server_key, client_key, label, mismatch_code, orphan_code in graph_checks:
        for interface in data["architecture"][graph_name]:
            endpoint_groups, scope = diagnostic_endpoint_groups([interface[server_key], interface[client_key]])
            if scope is None:
                continue
            servers, clients = endpoint_groups
            types = endpoint_types(endpoint_groups)
            selected_evidence = endpoint_evidence(endpoint_groups)
            if has_type_mismatch(types):
                deployed_together = endpoints_share_deployment(endpoint_groups, scope)
                diagnostics.append(
                    diagnostic(
                        mismatch_code,
                        "error" if deployed_together else "warning",
                        f"{label.title()} type mismatch",
                        f"{label.title()} '{interface['name']}' uses multiple static types in {scope} scope: {', '.join(types)}." + ("" if deployed_together else " The endpoints are not proven to run together."),
                        selected_evidence,
                        0.96 if deployed_together else 0.72,
                        interface=interface["name"],
                        deployment_scope=scope,
                        _remediation_context={"interface_types": types},
                    )
                )
            if not servers or not clients:
                missing_side = "server" if not servers else "client"
                diagnostics.append(
                    diagnostic(
                        orphan_code,
                        "info",
                        f"Orphan {label} endpoint",
                        f"{label.title()} '{interface['name']}' has no statically detected {scope} {missing_side}. Runtime or external nodes may provide it.",
                        selected_evidence,
                        0.62,
                        interface=interface["name"],
                        deployment_scope=scope,
                    )
                )
    root = Path(data["repository"]["path"])
    local_executables = {report["package"]["name"]: {item["name"] for item in report["executables"]} for report in reports}
    for launch_file in data["launch_graph"]["files"]:
        for include in launch_file["includes"]:
            if include.get("resolved") and include.get("exists") is False:
                diagnostics.append(diagnostic("RD301", "error", "Broken launch include", f"Launch include '{include.get('target')}' does not resolve to a file in the repository.", include["evidence"], 0.94, launch_file=launch_file["file"], package=launch_file["package"]))
        for action in launch_file["actions"]:
            for parameter in action.get("parameters", []):
                if parameter.get("kind") == "file" and parameter.get("resolved"):
                    resolved, exists = resolve_repository_reference(
                        parameter.get("value"),
                        launch_file["file"],
                        root,
                        [report["package"] for report in reports],
                        repository_files,
                    )
                    parameter["resolved_path"] = resolved
                    parameter["exists"] = exists
                    if exists is False:
                        diagnostics.append(diagnostic("RD302", "error", "Missing launch parameter file", f"Parameter file '{parameter.get('value')}' does not exist.", action["evidence"], 0.94, launch_file=launch_file["file"], package=launch_file["package"]))
            package_name = action.get("package")
            executable = action.get("executable")
            if action.get("kind") == "node" and package_name in local_executables and executable and action.get("resolved") and local_executables[package_name] and executable not in local_executables[package_name]:
                diagnostics.append(diagnostic("RD303", "warning", "Unknown local launch executable", f"Launch file references {package_name}/{executable}, but that executable was not found in the package build metadata.", action["evidence"], 0.78, launch_file=launch_file["file"], package=package_name, executable=executable))
    parent_by_model_child: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for transform in data["architecture"]["tf"]["transforms"]:
        if any(token in transform.get(field, "") for field in ("parent", "child") for token in ("${", "$(")):
            continue
        model_file = transform.get("file") or (transform.get("evidence") or [{}])[0].get("file", "")
        parent_by_model_child[(model_file, transform["child"])].append(transform)
    for (_, child), transforms in parent_by_model_child.items():
        parents = {item["parent"] for item in transforms}
        if len(parents) > 1:
            diagnostics.append(diagnostic("RD401", "error", "TF child has multiple parents", f"Frame '{child}' has multiple URDF parents: {', '.join(sorted(parents))}.", [item["evidence"][0] for item in transforms], 0.98, frame=child))
    graph = {transform["child"]: transform["parent"] for transform in data["architecture"]["tf"]["transforms"]}
    for start in graph:
        visited = set()
        current = start
        while current in graph:
            if current in visited:
                transform = next(item for item in data["architecture"]["tf"]["transforms"] if item["child"] == current)
                diagnostics.append(diagnostic("RD402", "error", "TF cycle detected", f"URDF frame graph contains a cycle involving '{current}'.", transform["evidence"], 0.98, frame=current))
                break
            visited.add(current)
            current = graph[current]
    return sorted(diagnostics, key=lambda item: ({"error": 0, "warning": 1, "info": 2}.get(item["severity"], 3), item["code"], item["message"]))


def empty_package_report(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "package": package,
        "launch_files": [],
        "launched_nodes": [],
        "executables": [],
        "node_names": [],
        "publishers": [],
        "subscriptions": [],
        "service_servers": [],
        "service_clients": [],
        "action_servers": [],
        "action_clients": [],
        "declared_parameters": [],
        "parameter_overrides": [],
        "parameter_files": [],
        "interfaces": [],
        "interface_files": {},
        "plugins": [],
        "tf_broadcasters": [],
        "urdf_files": [],
        "referenced_packages": [],
        "reference_evidence": {},
    }


def scan_repository(
    root: Path,
    *,
    config: ScanConfig | None = None,
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    selected_config = config or load_scan_config(repository=root)
    selected_config.validate()
    session = ScanSession(root=root, config=selected_config, progress=progress, cancel_check=cancel_check)
    token = _ACTIVE_SCAN_SESSION.set(session)
    try:
        data = _scan_repository(root)
        completed_at = datetime.now(timezone.utc)
        data["provenance"] = {
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
            "duration_seconds": round(time.perf_counter() - started, 6),
            "git": git_provenance(root),
            "input": {
                "source_type": "local",
                "archive_sha256": None,
                "content_sha256": None,
            },
            "ros_distribution": os.environ.get("ROS_DISTRO"),
            "environment": {
                "python_version": platform.python_version(),
                "platform": platform.system(),
                "platform_release": platform.release(),
                "architecture": platform.machine(),
            },
        }
        return data
    finally:
        _ACTIVE_SCAN_SESSION.reset(token)


def git_command(root: Path, *arguments: str, allow_empty: bool = False) -> str | None:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_COUNT": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-C",
                str(root),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and (value or allow_empty) else None


def git_supports_safe_fsmonitor_disable() -> bool:
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
            env={**os.environ, "GIT_CONFIG_COUNT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", result.stdout)
    return bool(result.returncode == 0 and match and (int(match.group(1)), int(match.group(2))) >= (2, 36))


def git_provenance(root: Path) -> dict[str, Any]:
    commit_sha = git_command(root, "rev-parse", "HEAD")
    if commit_sha is None:
        return {"commit_sha": None, "branch": None, "dirty": None}
    branch = git_command(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not git_supports_safe_fsmonitor_disable():
        return {"commit_sha": commit_sha, "branch": branch, "dirty": None}
    status = git_command(
        root,
        "status",
        "--porcelain",
        "--untracked-files=normal",
        "--ignore-submodules=all",
        allow_empty=True,
    )
    return {"commit_sha": commit_sha, "branch": branch, "dirty": None if status is None else bool(status)}


def load_scan_config_for_input(path: Path | None, repository: Path, source_type: str) -> ScanConfig:
    config_repository = repository if source_type == "local" else None
    return load_scan_config(path, config_repository)


def _scan_repository(root: Path) -> dict[str, Any]:
    session = _ACTIVE_SCAN_SESSION.get()
    if session is None:
        raise RuntimeError("scan session is not initialized")
    scan_files = collect_scan_files(root)
    session.emit("discover_packages", 0, 1, message="Discovering ROS packages")
    packages, initial_diagnostics = discover_packages(root, scan_files)
    session.emit("discover_packages", 1, 1, message=f"Discovered {len(packages)} package(s)")
    reports = [empty_package_report(package) for package in packages]
    report_by_path = {report["package"]["path"]: report for report in reports}
    uninstalled_targets: dict[str, set[str]] = defaultdict(set)
    launch_files = []
    urdf_transforms = []
    urdf_sensors = []
    urdf_control = {"hardware_components": [], "transmissions": [], "command_interfaces": [], "state_interfaces": []}
    urdf_frames = set()
    xacro_registry = discover_xacro_macros(scan_files, root, packages)
    for file_index, path in enumerate(scan_files, start=1):
        session.emit("scan_files", file_index - 1, len(scan_files), path, "Scanning source file")
        read_text(path)
        if path.resolve() in session.skipped_files:
            continue
        package = package_for_path(path, packages, root)
        report = report_by_path.get(package["path"]) if package else None
        if any(path.name.endswith(suffix) for suffix in LAUNCH_SUFFIXES) and report:
            actions, includes, arguments, issues = scan_launch_file(path, root)
            initial_diagnostics.extend(issues)
            for include in includes:
                resolved, exists = resolve_repository_reference(include.get("target"), include["source_file"], root, packages, scan_files)
                include["resolved_path"] = resolved
                include["exists"] = exists
            entry = {"file": relative(path, root), "format": "python" if path.name.endswith(".py") else "yaml" if path.name.endswith((".yaml", ".yml")) else "xml", "package": package["name"], "actions": actions, "includes": includes, "arguments": arguments, "fact_type": "detected", "confidence": 1.0, "evidence": [evidence(relative(path, root), 1, "launch_classifier", path.name)]}
            launch_files.append(entry)
            report["launch_files"].append({"file": entry["file"], "format": entry["format"], "confidence": 1.0, "fact_type": "detected", "evidence": entry["evidence"]})
            report["launched_nodes"].extend(action for action in actions if action["kind"] in {"node", "composable_node", "container"})
            for action in actions:
                if action.get("package"):
                    report["referenced_packages"].append(action["package"])
                    report["reference_evidence"].setdefault(action["package"], []).extend(action["evidence"])
            continue
        if not report:
            continue
        rel_file = relative(path, root)
        if path.name == "CMakeLists.txt":
            executables, references, uninstalled = scan_cmake(path, root, package["name"])
            report["executables"].extend(executables)
            report["referenced_packages"].extend(references)
            for reference, reference_items in references.items():
                report["reference_evidence"].setdefault(reference, []).extend(reference_items)
            uninstalled_targets[package["path"]].update(uninstalled)
        elif path.name == "setup.py":
            report["executables"].extend(scan_python_setup(path, root))
        elif path.name == "pyproject.toml":
            report["executables"].extend(scan_python_pyproject(path, root))
        elif path.name == "setup.cfg":
            report["executables"].extend(scan_python_setup_cfg(path, root))
        if path.suffix == ".py" and not path.name.endswith(".launch.py"):
            code, references, issues = scan_python_source(path, root)
            initial_diagnostics.extend(issues)
            for key, values in code.items():
                report.setdefault(key, []).extend(values)
            report["referenced_packages"].extend(references)
            for reference in references:
                report["reference_evidence"].setdefault(reference, []).append(evidence(rel_file, 1, "python_import", reference))
        elif path.suffix in SOURCE_EXTENSIONS and path.suffix != ".py":
            code, references = scan_cpp_source(path, root)
            for key, values in code.items():
                report.setdefault(key, []).extend(values)
            report["referenced_packages"].extend(references)
            for reference in references:
                report["reference_evidence"].setdefault(reference, []).append(evidence(rel_file, 1, "cpp_include", reference))
        if path.suffix in {".msg", ".srv", ".action"}:
            interface = parse_interface(path, root)
            report["interfaces"].append(interface)
            key = {".msg": "messages", ".srv": "services", ".action": "actions"}[path.suffix]
            report["interface_files"].setdefault(key, []).append(interface["file"])
        if path.suffix in {".yaml", ".yml"} and not path.name.endswith((".launch.yaml", ".launch.yml")):
            overrides = scan_parameter_yaml(path, root)
            if overrides:
                report["parameter_overrides"].extend(overrides)
                selectors = sorted(
                    {
                        (item.get("selector") or "/**", item.get("namespace") or "", item.get("selector_specificity", 0))
                        for item in overrides
                    }
                )
                report["parameter_files"].append(
                    {
                        "file": rel_file,
                        "selectors": [
                            {"selector": selector, "namespace": namespace, "specificity": specificity}
                            for selector, namespace, specificity in selectors
                        ],
                        "parameters": overrides,
                        "fact_type": "detected",
                        "confidence": 0.96,
                        "evidence": [evidence(rel_file, 1, "yaml_parameter_tree", "ros__parameters")],
                    }
                )
            report["plugins"].extend(scan_plugins(path, root))
        elif path.suffix == ".xml" and path.name != "package.xml":
            report["plugins"].extend(scan_plugins(path, root))
        if path.suffix in {".urdf", ".xacro"}:
            transforms, sensors, frames, control_model = scan_urdf(path, root, xacro_registry)
            for sensor in sensors:
                sensor["package"] = package["name"]
                sensor["deployment_scope"] = deployment_scope(sensor.get("file"), package["name"])
            for entries in control_model.values():
                for item in entries:
                    item["package"] = package["name"]
                    item["deployment_scope"] = deployment_scope(item.get("file"), package["name"])
            urdf_transforms.extend(transforms)
            urdf_sensors.extend(sensors)
            for key, entries in control_model.items():
                urdf_control[key].extend(entries)
            urdf_frames.update(frames)
            report["urdf_files"].append(rel_file)
    entity_keys = ("executables", "node_names", "publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients", "declared_parameters", "parameter_overrides", "plugins", "tf_broadcasters")
    for report in reports:
        cpp_paths = [path for path in scan_files if path.resolve() not in session.skipped_files and package_for_path(path, packages, root) == report["package"] and path.suffix in SOURCE_EXTENSIONS and path.suffix != ".py"]
        wrapper_models, factory_locations = discover_cpp_wrapper_models(cpp_paths, root)
        method_wrapper_models, method_factory_locations = discover_cpp_method_wrapper_models(cpp_paths, root)
        factory_locations.update(method_factory_locations)
        wrapper_findings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for path in cpp_paths:
            session.check_cancelled()
            for key, values in scan_cpp_wrapper_instantiations(path, root, wrapper_models).items():
                wrapper_findings[key].extend(values)
            for key, values in scan_cpp_method_wrapper_invocations(path, root, method_wrapper_models).items():
                wrapper_findings[key].extend(values)
        for key, values in wrapper_findings.items():
            report[key] = [item for item in report[key] if (item.get("file"), item.get("line")) not in factory_locations]
            report[key].extend(values)
        for key in entity_keys:
            report[key] = unique_findings(report[key])
        report["launched_nodes"] = sorted(report["launched_nodes"], key=lambda item: (item["source_file"], item.get("line") or 0))
        report["referenced_packages"] = sorted(set(report["referenced_packages"]))
        report["urdf_files"] = sorted(set(report["urdf_files"]))
    nodes = build_node_architecture(reports, launch_files, root, scan_files)
    topics = communication_architecture(nodes, "publishers", "subscriptions", "publishers", "subscribers")
    services = communication_architecture(nodes, "service_servers", "service_clients", "servers", "clients")
    actions = communication_architecture(nodes, "action_servers", "action_clients", "servers", "clients")
    ros2_control = build_ros2_control_model(reports, urdf_control)
    architecture = infer_architecture(reports, urdf_sensors, ros2_control)
    architecture.update({"nodes": nodes, "topics": topics, "services": services, "actions": actions, "tf": {"frames": sorted(urdf_frames), "transforms": urdf_transforms, "broadcasters": [item for report in reports for item in report["tf_broadcasters"]]}, "ros2_control": ros2_control, "modification_points": modification_points(reports, ros2_control)})
    data = {
        "schema_version": SCHEMA_VERSION,
        "scanner": {"name": "robot-doctor-static", "version": SCANNER_VERSION, "mode": "static", "fact_model": ["detected", "inferred", "diagnostic"]},
        "repository": {"path": str(root), "name": root.name},
        "configuration": session.config.to_output(),
        "package_count": len(packages),
        "packages": reports,
        "launch_graph": {"files": launch_files, "edges": [{"from": launch["file"], "to": include.get("resolved_path") or include.get("target"), "resolved": include.get("exists") is True, "confidence": include["confidence"], "evidence": include["evidence"]} for launch in launch_files for include in launch["includes"]]},
        "architecture": architecture,
        "diagnostics": [],
        "limitations": [
            "Static analysis cannot prove runtime names, substitutions, remappings, plugin loading, or external nodes.",
            "Orphan endpoint and dependency diagnostics may be satisfied by another repository or installed package.",
            "Static source extraction cannot prove whether endpoints in mutually exclusive branches execute together; same-node type conflicts remain advisory unless distinct unconditional launch instances prove co-deployment.",
            "YAML launch parsing covers standard declarative ROS 2 forms but preserves unsupported dynamic expressions as unresolved.",
            "Best-effort Xacro expansion resolves declarative include-visible macros, arguments, and properties; dynamic includes, conditionals, arbitrary expressions, and executable extensions remain unresolved and are never executed.",
            "Runtime TF and QoS behavior should be confirmed on a running system.",
            "Static control chains use package and deployment scope to prevent unsafe cross-robot links and report equally ranked hardware candidates as ambiguous; they do not prove controller loading, hardware activation, successful builds, or runtime communication.",
        ],
    }
    initial_diagnostics.extend(session.input_diagnostics)
    raw_diagnostics = run_diagnostics(data, uninstalled_targets, initial_diagnostics, scan_files)
    data["diagnostics"], suppressed_diagnostics = session.config.apply_diagnostic_policy(raw_diagnostics)
    data["configuration"] = session.config.to_output(suppressed_diagnostics)
    severities = defaultdict(int)
    for item in data["diagnostics"]:
        severities[item["severity"]] += 1
    inventory_keys = ("executables", "node_names", "publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients", "declared_parameters")
    inventory_entities = [item for report in reports for key in inventory_keys for item in report[key]]
    active_nodes = [node for node in nodes if node.get("active")]
    node_scopes = {scope: sum(node.get("deployment_scope") == scope for node in active_nodes) for scope in DEPLOYMENT_SCOPES}
    data["summary"] = {
        "packages": len(reports),
        "launch_files": len(launch_files),
        "nodes": len(active_nodes),
        "architecture_nodes_total": len(nodes),
        "node_scopes": node_scopes,
        "topics": len(topics),
        "services": len(services),
        "actions": len(actions),
        "resolved_entities": sum(1 for item in inventory_entities if item.get("resolved", True)),
        "unresolved_entities": sum(1 for item in inventory_entities if not item.get("resolved", True)),
        "skipped_files": len(session.skipped_files),
        "diagnostics": dict(sorted(severities.items())),
    }
    session.emit("complete", len(scan_files), len(scan_files), message="Scan complete")
    return data


def entity_label(entity: dict[str, Any]) -> str:
    name = entity.get("name") or "<unresolved>"
    type_name = f" [{entity['type']}]" if entity.get("type") else ""
    confidence = f" confidence={entity.get('confidence', 0):.2f}"
    location = f" ({entity.get('file')}:{entity.get('line')})" if entity.get("file") else ""
    return f"{name}{type_name}{confidence}{location}"


def print_text_report(data: dict[str, Any], *, resolved_only: bool = False) -> None:
    print(f"Repository: {data['repository']['path']}")
    print(f"Schema: {data['schema_version']}  Scanner: {data['scanner']['version']} ({data['scanner']['mode']})")
    provenance = data.get("provenance") or {}
    git = provenance.get("git") or {}
    print(
        f"Scanned: {provenance.get('completed_at') or 'unknown'} in {provenance.get('duration_seconds', 0):.3f}s; "
        f"Git: {git.get('commit_sha') or 'not detected'}"
    )
    print(f"Packages: {data['package_count']}")
    print(
        f"Entities: {data['summary']['resolved_entities']} resolved, "
        f"{data['summary']['unresolved_entities']} unresolved; skipped files: {data['summary']['skipped_files']}"
    )
    print(
        f"Diagnostic policy: dependency_mode={data['configuration']['dependency_mode']}, "
        f"suppressed={data['configuration']['suppressed_diagnostics']}"
    )
    for report in data["packages"]:
        package = report["package"]
        print(f"\n{package['name']} ({package['path']})")
        print(f"  build_type: {package.get('build_type') or '<unspecified>'}")
        for key in ("executables", "node_names", "publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients", "declared_parameters"):
            if report[key]:
                print(f"  {key}:")
                for item in report[key]:
                    if resolved_only and not item.get("resolved", True):
                        continue
                    print(f"    - {entity_label(item)}")
        if report["launch_files"]:
            print("  launch_files:")
            for item in report["launch_files"]:
                print(f"    - {item['file']} [{item['format']}]")
    if data["diagnostics"]:
        print("\nDiagnostics:")
        for item in data["diagnostics"]:
            print(f"  - {item['severity'].upper()} {item['code']}: {item['message']} (confidence={item['confidence']:.2f})")
            print(f"    Repair: {item['remediation']['summary']}")
            for command in item["remediation"]["commands"]:
                print(f"    Command: {command}")
    print("\nLimitations:")
    for limitation in data["limitations"]:
        print(f"  - {limitation}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Statically discover and diagnose a ROS 2 repository or workspace.")
    parser.add_argument("repository", nargs="?", default=".", help="Repository/workspace path or HTTPS Git URL")
    parser.add_argument("--json", action="store_true", help="Emit the stable JSON schema instead of text")
    parser.add_argument("--output", "-o", help="Write output to a file")
    parser.add_argument("--config", type=Path, help="JSON configuration file; local scans default to <repository>/.robot-doctor.json")
    parser.add_argument("--suppress", action="append", default=[], metavar="CODE[,CODE]", help="Suppress diagnostic codes")
    parser.add_argument("--severity", action="append", default=[], metavar="CODE=LEVEL", help="Override diagnostic severity")
    parser.add_argument("--dependency-mode", choices=("off", "direct", "all"), help="Dependency diagnostic sensitivity")
    parser.add_argument("--max-file-size-mb", type=float, help="Maximum source-file size in MiB")
    parser.add_argument("--max-total-size-mb", type=float, help="Maximum total source text read in MiB")
    parser.add_argument("--max-files", type=int, help="Maximum number of scan-candidate files to select and read")
    parser.add_argument("--max-repository-entries", type=int, help="Maximum files and directories to enumerate")
    parser.add_argument("--max-checkout-size-mb", type=float, help="Maximum HTTPS Git checkout size in MiB")
    parser.add_argument("--git-token-env", metavar="ENV_VAR", help="Read a private-repository access token from this environment variable")
    parser.add_argument("--progress", action="store_true", help="Report progress to stderr")
    parser.add_argument("--resolved-only", action="store_true", help="Hide unresolved entities in text output")
    args = parser.parse_args()
    suppressions = {code.strip() for value in args.suppress for code in value.split(",") if code.strip()}
    severity_overrides = {}
    for value in args.severity:
        if "=" not in value:
            parser.error(f"severity override must be CODE=LEVEL: {value}")
        code, severity = value.split("=", 1)
        severity_overrides[code.strip()] = severity.strip()
    if args.max_file_size_mb is not None and args.max_file_size_mb <= 0:
        parser.error("--max-file-size-mb must be positive")
    if args.max_total_size_mb is not None and args.max_total_size_mb <= 0:
        parser.error("--max-total-size-mb must be positive")
    if args.max_files is not None and args.max_files <= 0:
        parser.error("--max-files must be positive")
    if args.max_repository_entries is not None and args.max_repository_entries <= 0:
        parser.error("--max-repository-entries must be positive")
    if args.max_checkout_size_mb is not None and args.max_checkout_size_mb <= 0:
        parser.error("--max-checkout-size-mb must be positive")

    def progress(event: dict[str, Any]) -> None:
        if args.progress:
            total = event["total"] or 1
            print(f"[{event['stage']}] {event['current']}/{total} {event.get('path') or ''} {event.get('message') or ''}".rstrip(), file=sys.stderr)

    from .intake import IntakeError, materialize_repository

    try:
        access_token = None
        if args.git_token_env:
            access_token = os.environ.get(args.git_token_env)
            if not access_token:
                parser.error(f"environment variable {args.git_token_env!r} is not set or is empty")
        with materialize_repository(
            args.repository,
            max_checkout_bytes=int(args.max_checkout_size_mb * 1024 * 1024) if args.max_checkout_size_mb is not None else None,
            access_token=access_token,
        ) as repository_input:
            selected_config = load_scan_config_for_input(
                args.config,
                repository_input.path,
                repository_input.source_type,
            ).with_overrides(
                max_file_size_bytes=int(args.max_file_size_mb * 1024 * 1024) if args.max_file_size_mb is not None else None,
                max_total_size_bytes=int(args.max_total_size_mb * 1024 * 1024) if args.max_total_size_mb is not None else None,
                max_files=args.max_files,
                max_repository_entries=args.max_repository_entries,
                dependency_mode=args.dependency_mode,
                suppress_diagnostics=suppressions,
                severity_overrides=severity_overrides,
            )
            data = scan_repository(repository_input.path, config=selected_config, progress=progress)
            data["repository"].update(source=repository_input.source, source_type=repository_input.source_type)
            data["provenance"]["input"]["source_type"] = repository_input.source_type
    except (ConfigError, IntakeError) as exc:
        parser.error(str(exc))
    except ScanCancelled:
        print("Scan cancelled.", file=sys.stderr)
        return 130
    if args.json:
        output = json.dumps(data, indent=2, sort_keys=True)
    else:
        from io import StringIO

        buffer = StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            print_text_report(data, resolved_only=args.resolved_only)
        finally:
            sys.stdout = original
        output = buffer.getvalue().rstrip()
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
