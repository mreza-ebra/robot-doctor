from __future__ import annotations

import argparse
import hashlib
import html
import hmac
import ipaddress
import json
import os
import secrets
import socket
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ConfigError, ScanConfig
from .intake import DEFAULT_MAX_CHECKOUT_BYTES, IntakeError, clone_git_repository, extract_zip_upload, repository_content_sha256
from .overviews import basic_document, expert_document, intermediate_document
from .scanner import ScanCancelled, scan_repository

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_CONCURRENT_TASKS = 2
ACTIVE_TASK_STATUSES = {"queued", "intake", "scanning"}


@dataclass
class ScanTask:
    id: str
    source: str
    source_type: str
    directory: Path
    config: ScanConfig
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=lambda: {"stage": "queued", "current": 0, "total": 1, "message": "Waiting to start"})
    error: str | None = None
    summary: dict[str, Any] | None = None
    archive_sha256: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class WebApplication:
    def __init__(
        self,
        *,
        max_concurrent_tasks: int = DEFAULT_MAX_CONCURRENT_TASKS,
        max_checkout_bytes: int = DEFAULT_MAX_CHECKOUT_BYTES,
    ) -> None:
        if max_concurrent_tasks <= 0:
            raise ValueError("max_concurrent_tasks must be positive")
        if max_checkout_bytes <= 0:
            raise ValueError("max_checkout_bytes must be positive")
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="robot-doctor-web-")
        self.root = Path(self.temporary_directory.name)
        self.tasks: dict[str, ScanTask] = {}
        self.lock = threading.Lock()
        self.max_concurrent_tasks = max_concurrent_tasks
        self.max_checkout_bytes = max_checkout_bytes
        self.csrf_token = secrets.token_urlsafe(32)

    def close(self) -> None:
        for task in self.tasks.values():
            task.cancel_event.set()
        for task in self.tasks.values():
            if task.thread and task.thread.is_alive():
                task.thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def submit_git(self, url: str, config: ScanConfig, access_token: str | None = None) -> ScanTask:
        task = self._new_task(url, "git", config)
        task.thread = threading.Thread(target=self._run_git_task, args=(task, access_token), daemon=True)
        task.thread.start()
        return task

    def submit_upload(self, filename: str, payload: bytes, config: ScanConfig) -> ScanTask:
        if len(payload) > MAX_UPLOAD_BYTES:
            raise IntakeError(f"upload exceeds the {MAX_UPLOAD_BYTES}-byte limit")
        task = self._new_task(filename or "repository.zip", "upload", config)
        task.archive_sha256 = hashlib.sha256(payload).hexdigest()
        archive = task.directory / "upload.zip"
        archive.write_bytes(payload)
        task.thread = threading.Thread(target=self._run_upload_task, args=(task, archive), daemon=True)
        task.thread.start()
        return task

    def _new_task(self, source: str, source_type: str, config: ScanConfig) -> ScanTask:
        with self.lock:
            active_tasks = sum(task.status in ACTIVE_TASK_STATUSES for task in self.tasks.values())
            if active_tasks >= self.max_concurrent_tasks:
                raise IntakeError(
                    f"the local task limit of {self.max_concurrent_tasks} active scan(s) has been reached"
                )
            identifier = uuid.uuid4().hex
            directory = self.root / identifier
            directory.mkdir()
            task = ScanTask(identifier, source, source_type, directory, config)
            self.tasks[identifier] = task
        return task

    def _run_git_task(self, task: ScanTask, access_token: str | None) -> None:
        try:
            self._update(task, status="intake", progress={"stage": "clone", "current": 0, "total": 1, "message": "Cloning repository"})
            repository = clone_git_repository(
                task.source,
                task.directory / "repository",
                cancel_check=task.cancel_event.is_set,
                max_checkout_bytes=self.max_checkout_bytes,
                access_token=access_token,
            )
            self._scan(task, repository)
        except (IntakeError, ConfigError, OSError) as exc:
            self._fail(task, str(exc))
        except ScanCancelled:
            self._update(task, status="cancelled", progress={"stage": "cancelled", "current": 0, "total": 1, "message": "Cancelled"})
        except Exception as exc:
            self._fail(task, f"unexpected scan failure: {exc}")

    def _run_upload_task(self, task: ScanTask, archive: Path) -> None:
        try:
            self._update(task, status="intake", progress={"stage": "extract", "current": 0, "total": 1, "message": "Extracting ZIP archive"})
            repository = extract_zip_upload(archive, task.directory / "repository")
            content_sha256 = repository_content_sha256(repository)
            self._scan(task, repository, content_sha256=content_sha256)
        except (IntakeError, ConfigError, OSError) as exc:
            self._fail(task, str(exc))
        except ScanCancelled:
            self._update(task, status="cancelled", progress={"stage": "cancelled", "current": 0, "total": 1, "message": "Cancelled"})
        except Exception as exc:
            self._fail(task, f"unexpected scan failure: {exc}")

    def _scan(self, task: ScanTask, repository: Path, *, content_sha256: str | None = None) -> None:
        self._update(task, status="scanning")

        def progress(event: dict[str, Any]) -> None:
            self._update(task, progress=event)

        data = scan_repository(repository, config=task.config, progress=progress, cancel_check=task.cancel_event.is_set)
        data["repository"].update(source=task.source, source_type=task.source_type)
        data["provenance"]["input"] = {
            "source_type": task.source_type,
            "archive_sha256": task.archive_sha256,
            "content_sha256": content_sha256,
        }
        (task.directory / "result.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        documents = {
            "basic.md": basic_document(repository, data),
            "intermediate.md": intermediate_document(repository, data),
            "expert.md": expert_document(repository, data),
        }
        for filename, content in documents.items():
            (task.directory / filename).write_text(content.rstrip() + "\n", encoding="utf-8")
        (task.directory / "result.html").write_text(
            page("Robot Doctor Results", result_body(data)),
            encoding="utf-8",
        )
        self._update(task, status="complete", summary=data["summary"], progress={"stage": "complete", "current": 1, "total": 1, "message": "Complete"})

    def _fail(self, task: ScanTask, error: str) -> None:
        self._update(task, status="failed", error=error, progress={"stage": "failed", "current": 0, "total": 1, "message": error})

    def _update(self, task: ScanTask, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(task, key, value)


def page(title: str, body: str, *, refresh: int | None = None) -> str:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">{refresh_tag}<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font-family:Inter,ui-sans-serif,system-ui,sans-serif;max-width:1180px;margin:2rem auto;padding:0 1rem;color:#172033;background:#fbfcff}}a{{color:#174bb5}}h1,h2,h3{{line-height:1.2}}fieldset,.panel,.finding{{border:1px solid #ccd4e0;border-radius:12px;padding:1rem;margin:1rem 0;background:white}}input,select,button{{font:inherit;padding:.65rem;margin:.25rem 0}}input[type=text],input[type=url],input[type=password]{{width:min(100%,700px);box-sizing:border-box}}button{{background:#2156d9;color:white;border:0;border-radius:7px;cursor:pointer}}.muted{{color:#596579}}.error{{color:#a21b1b}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:.75rem}}.metric{{border:1px solid #d8dee8;border-radius:10px;padding:.8rem;background:white}}.metric strong{{display:block;font-size:1.65rem}}.badge{{display:inline-block;border-radius:999px;padding:.18rem .55rem;font-size:.78rem;font-weight:700;text-transform:uppercase}}.badge-error{{background:#fee2e2;color:#991b1b}}.badge-warning{{background:#fef3c7;color:#92400e}}.badge-info{{background:#dbeafe;color:#1e40af}}.finding-error{{border-left:5px solid #dc2626}}.finding-warning{{border-left:5px solid #d97706}}.finding-info{{border-left:5px solid #2563eb}}.finding header,.finding>summary{{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}}.finding h3{{margin:.25rem 0}}.finding ol{{margin-top:.4rem}}.finding-body{{padding-top:.25rem}}.filters{{display:flex;gap:.75rem;align-items:end;flex-wrap:wrap;padding:.75rem;border:1px solid #d8dee8;border-radius:10px;background:#f8fafc}}.filters label{{display:grid;gap:.2rem}}table{{border-collapse:collapse;width:100%;display:block;overflow:auto}}td,th{{border:1px solid #d8dee8;padding:.45rem;text-align:left;vertical-align:top}}th{{background:#f4f6fa}}pre{{white-space:pre-wrap;background:#f1f5f9;padding:.8rem;border-radius:8px;overflow:auto}}code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}details{{margin:.65rem 0}}summary{{cursor:pointer;font-weight:650}}.diagram{{width:100%;height:auto;border:1px solid #d8dee8;border-radius:12px;background:white}}.downloads{{display:flex;gap:.8rem;flex-wrap:wrap}}progress{{width:min(100%,700px);height:1.2rem}}@media(max-width:650px){{body{{margin:1rem auto}}.finding{{padding:.75rem}}}}
</style></head><body><h1>{html.escape(title)}</h1>{body}</body></html>"""


def architecture_visual(data: dict[str, Any]) -> str:
    endpoint_models = {
        "publishers": ("topic", "out"),
        "subscriptions": ("topic", "in"),
        "service_servers": ("service", "in"),
        "service_clients": ("service", "out"),
        "action_servers": ("action", "in"),
        "action_clients": ("action", "out"),
    }

    def endpoint_count(node: dict[str, Any]) -> int:
        return sum(len(node.get(key, [])) for key in endpoint_models)

    all_nodes = [
        node
        for node in data.get("architecture", {}).get("nodes", [])
        if node.get("active") and (node.get("name") or node.get("executable"))
    ]
    nodes = sorted(
        all_nodes,
        key=lambda node: (-endpoint_count(node), str(node.get("package") or ""), str(node.get("name") or "")),
    )[:8]
    candidate_edges: list[tuple[str, tuple[str, str], str]] = []
    interface_frequency: dict[tuple[str, str], int] = {}
    for node in nodes:
        for key, (kind, direction) in endpoint_models.items():
            for endpoint in node.get(key, []):
                name = endpoint.get("name")
                if not name or not endpoint.get("resolved", True):
                    continue
                interface_key = (kind, str(name))
                candidate_edges.append((str(node["id"]), interface_key, direction))
                interface_frequency[interface_key] = interface_frequency.get(interface_key, 0) + 1
    interfaces = sorted(
        interface_frequency,
        key=lambda item: (-interface_frequency[item], item[0], item[1]),
    )[:14]
    selected_interfaces = set(interfaces)
    edges = [edge for edge in candidate_edges if edge[1] in selected_interfaces]
    row_count = max(len(nodes), len(interfaces), 1)
    row_gap = 70
    body_top = 95
    height = body_top + row_count * row_gap + 95

    def compact(value: Any, limit: int = 30) -> str:
        text = str(value or "")
        return text if len(text) <= limit else text[: limit - 1] + "…"

    node_positions = {str(node["id"]): body_top + index * row_gap for index, node in enumerate(nodes)}
    interface_positions = {interface: body_top + index * row_gap + 25 for index, interface in enumerate(interfaces)}
    edge_svg = "".join(
        (
            f'<line x1="300" y1="{node_positions[node_id] + 25}" x2="610" y2="{interface_positions[interface]}" stroke="#94a3b8" stroke-width="1.6" marker-end="url(#topology-arrow)"/>'
            if direction == "out"
            else f'<line x1="610" y1="{interface_positions[interface]}" x2="300" y2="{node_positions[node_id] + 25}" stroke="#94a3b8" stroke-width="1.6" marker-end="url(#topology-arrow)"/>'
        )
        for node_id, interface, direction in edges
    )
    node_svg = "".join(
        f'<g><title>{html.escape(str(node.get("namespace") or "/") + "/" + str(node.get("name") or node.get("executable") or ""))}</title>'
        f'<rect x="30" y="{node_positions[str(node["id"])]}" width="270" height="50" rx="8" fill="#eef2ff" stroke="#6366f1"/>'
        f'<text x="45" y="{node_positions[str(node["id"])] + 21}" font-size="14" font-weight="700" fill="#172033">{html.escape(compact(node.get("name") or node.get("executable") or "unresolved"))}</text>'
        f'<text x="45" y="{node_positions[str(node["id"])] + 40}" font-size="11" fill="#596579">{html.escape(compact(node.get("package") or "external", 34))}</text></g>'
        for node in nodes
    )
    interface_svg_parts = []
    colors = {"topic": ("#ecfdf5", "#059669"), "service": ("#fff7ed", "#ea580c"), "action": ("#fdf2f8", "#db2777")}
    for kind, name in interfaces:
        y = interface_positions[(kind, name)]
        fill, stroke = colors[kind]
        if kind == "topic":
            shape = f'<ellipse cx="700" cy="{y}" rx="90" ry="24" fill="{fill}" stroke="{stroke}"/>'
        else:
            dash = ' stroke-dasharray="5 3"' if kind == "action" else ""
            shape = f'<rect x="610" y="{y - 24}" width="180" height="48" rx="14" fill="{fill}" stroke="{stroke}"{dash}/>'
        interface_svg_parts.append(
            f'<g><title>{html.escape(name)}</title>{shape}'
            f'<text x="700" y="{y - 2}" text-anchor="middle" font-size="12" font-weight="700" fill="#172033">{html.escape(compact(name, 28))}</text>'
            f'<text x="700" y="{y + 14}" text-anchor="middle" font-size="10" fill="#596579">{kind}</text></g>'
        )
    if not nodes:
        node_svg = '<text x="410" y="150" text-anchor="middle" font-size="15" fill="#596579">No active source or launched nodes were detected.</text>'
    note = f"Showing {len(nodes)} of {len(all_nodes)} active nodes and {len(interfaces)} connected interfaces. Arrows show endpoint direction."
    return (
        f'<svg class="diagram" role="img" aria-label="Node and interface topology" viewBox="0 0 830 {height}">'
        '<defs><marker id="topology-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#64748b"/></marker></defs>'
        '<text x="415" y="34" text-anchor="middle" font-size="18" font-weight="700" fill="#172033">Node and interface topology</text>'
        '<text x="165" y="68" text-anchor="middle" font-size="13" font-weight="700" fill="#334155">Nodes</text>'
        '<text x="700" y="68" text-anchor="middle" font-size="13" font-weight="700" fill="#334155">Topics, services, and actions</text>'
        f'{edge_svg}{node_svg}{"".join(interface_svg_parts)}'
        f'<text x="415" y="{height - 34}" text-anchor="middle" font-size="11" fill="#596579">{html.escape(note)}</text>'
        '</svg>'
    )


def metric_cards(summary: dict[str, Any]) -> str:
    keys = ("packages", "launch_files", "nodes", "topics", "services", "actions", "resolved_entities", "unresolved_entities")
    return '<div class="grid">' + "".join(
        f'<div class="metric"><span>{html.escape(key.replace("_", " ").title())}</span><strong>{html.escape(str(summary.get(key, 0)))}</strong></div>'
        for key in keys
    ) + "</div>"


def diagnostic_card(item: dict[str, Any]) -> str:
    severity = str(item.get("severity") or "info")
    remediation = item.get("remediation") or {}
    steps = "".join(f"<li>{html.escape(str(step))}</li>" for step in remediation.get("steps", []))
    commands = "\n".join(str(command) for command in remediation.get("commands", []))
    files = ", ".join(str(path) for path in remediation.get("suggested_files", []))
    evidence_items = item.get("evidence") or []
    evidence_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(evidence.get('file') or ''))}</td>"
        f"<td>{html.escape(str(evidence.get('line') or ''))}</td>"
        f"<td>{html.escape(str(evidence.get('extractor') or ''))}</td>"
        f"<td>{html.escape(str(evidence.get('snippet') or ''))}</td>"
        "</tr>"
        for evidence in evidence_items
    )
    patch_hint = remediation.get("patch_hint")
    details = ""
    if commands:
        details += f"<h4>Commands to verify</h4><pre><code>{html.escape(commands)}</code></pre>"
    if files:
        details += f"<p><strong>Suggested files:</strong> {html.escape(files)}</p>"
    if patch_hint:
        details += f"<h4>Patch hint</h4><pre><code>{html.escape(str(patch_hint))}</code></pre>"
    if evidence_rows:
        details += (
            '<details><summary>Evidence</summary><table><thead><tr><th>File</th><th>Line</th><th>Extractor</th><th>Snippet</th></tr></thead>'
            f"<tbody>{evidence_rows}</tbody></table></details>"
        )
    badge = f'<span class="badge badge-{html.escape(severity)}">{html.escape(severity)}</span>'
    metadata = f'<strong>{html.escape(str(item.get("code") or ""))}</strong><span>confidence {float(item.get("confidence", 0)):.0%}</span>'
    content = (
        f'<div class="finding-body"><p>{html.escape(str(item.get("message") or ""))}</p>'
        f'<p><strong>Recommended repair:</strong> {html.escape(str(remediation.get("summary") or "Review the evidence and rescan."))}</p>'
        f"<ol>{steps}</ol>{details}</div>"
    )
    title = html.escape(str(item.get("title") or "Finding"))
    if severity == "info":
        return f'<details class="finding finding-info"><summary>{badge}{metadata}<span>{title}</span></summary>{content}</details>'
    return f'<article class="finding finding-{html.escape(severity)}"><header>{badge}{metadata}</header><h3>{title}</h3>{content}</article>'


def node_table(data: dict[str, Any], limit: int = 30) -> str:
    nodes = sorted(
        data.get("architecture", {}).get("nodes", []),
        key=lambda item: (not item.get("active", False), str(item.get("package") or ""), str(item.get("name") or "")),
    )[:limit]
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('name') or item.get('executable') or '<unresolved>'))}</td>"
        f"<td>{html.escape(str(item.get('package') or ''))}</td>"
        f"<td>{html.escape(str(item.get('namespace') or '/'))}</td>"
        f"<td>{html.escape(str(item.get('origin') or ''))}</td>"
        f"<td>{sum(len(item.get(key, [])) for key in ('publishers', 'subscriptions', 'service_servers', 'service_clients', 'action_servers', 'action_clients'))}</td>"
        "</tr>"
        for item in nodes
    )
    return rows or '<tr><td colspan="5">No source or launched nodes detected.</td></tr>'


def interface_table(data: dict[str, Any], graph_name: str, left: str, right: str, limit: int = 30) -> str:
    interfaces = data.get("architecture", {}).get(graph_name, [])[:limit]
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('name') or '<unresolved>'))}</td>"
        f"<td>{html.escape(', '.join(str(value) for value in item.get('types', [])))}</td>"
        f"<td>{len(item.get(left, []))}</td><td>{len(item.get(right, []))}</td>"
        "</tr>"
        for item in interfaces
    )
    return rows or '<tr><td colspan="4">None detected.</td></tr>'


def role_table(data: dict[str, Any], limit: int = 40) -> str:
    architecture = data.get("architecture") or {}
    rows = []
    for category in ("sensors", "algorithms", "actuation"):
        for item in architecture.get(category, []):
            rows.append(
                "<tr>"
                f"<td>{html.escape(category[:-1].title() if category.endswith('s') else category.title())}</td>"
                f"<td>{html.escape(str(item.get('name') or '<unresolved>'))}</td>"
                f"<td>{html.escape(str(item.get('type') or ''))}</td>"
                f"<td>{html.escape(str(item.get('role') or ''))}</td>"
                f"<td>{html.escape(str(item.get('package') or ''))}</td>"
                f"<td>{html.escape(str(item.get('file') or ''))}</td>"
                "</tr>"
            )
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break
    return "".join(rows) or '<tr><td colspan="6">No sensor, algorithm, or actuation role was inferred from source evidence.</td></tr>'


def modification_table(data: dict[str, Any], limit: int = 30) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('task') or ''))}</td>"
        f"<td>{html.escape(str(item.get('package') or ''))}</td>"
        f"<td>{html.escape(str(item.get('path') or ''))}</td>"
        f"<td>{html.escape(str(item.get('reason') or ''))}</td>"
        "</tr>"
        for item in data.get("architecture", {}).get("modification_points", [])[:limit]
    )
    return rows or '<tr><td colspan="4">No evidence-backed modification point was inferred.</td></tr>'


def provenance_table(data: dict[str, Any]) -> str:
    provenance = data.get("provenance") or {}
    git = provenance.get("git") or {}
    input_provenance = provenance.get("input") or {}
    environment = provenance.get("environment") or {}
    values = [
        ("Started", provenance.get("started_at")),
        ("Completed", provenance.get("completed_at")),
        ("Duration", f"{float(provenance.get('duration_seconds', 0)):.3f} seconds"),
        ("Commit", git.get("commit_sha") or "Not detected"),
        ("Branch", git.get("branch") or "Detached or not detected"),
        ("Dirty working tree", git.get("dirty")),
        ("Input type", input_provenance.get("source_type")),
        ("Archive SHA-256", input_provenance.get("archive_sha256") or "Not applicable"),
        ("Content SHA-256", input_provenance.get("content_sha256") or "Not calculated"),
        ("ROS distribution", provenance.get("ros_distribution") or "Not sourced"),
        ("Python", environment.get("python_version")),
        ("Platform", " ".join(str(environment.get(key) or "") for key in ("platform", "platform_release", "architecture")).strip()),
    ]
    return "".join(f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>" for label, value in values)


def diagnostic_packages(item: dict[str, Any], data: dict[str, Any]) -> set[str]:
    packages = {str(item["package"])} if item.get("package") else set()
    evidence_files = {str(entry.get("file") or "") for entry in item.get("evidence", [])}
    for report in data.get("packages", []):
        package = report.get("package") or {}
        name = package.get("name")
        package_path = str(package.get("path") or "")
        if not name:
            continue
        if package_path == "." and evidence_files:
            packages.add(str(name))
        elif package_path and any(path == package_path or path.startswith(package_path + "/") for path in evidence_files):
            packages.add(str(name))
    return packages


def findings_filter_form(data: dict[str, Any], link_prefix: str, severity_filter: str, package_filter: str) -> str:
    if not link_prefix:
        return ""
    package_names = sorted(str(report["package"]["name"]) for report in data.get("packages", []))
    severity_options = "".join(
        f'<option value="{value}"{" selected" if severity_filter == value else ""}>{label}</option>'
        for value, label in (("all", "All severities"), ("error", "Errors"), ("warning", "Warnings"), ("info", "Information"))
    )
    package_options = '<option value="">All packages</option>' + "".join(
        f'<option value="{html.escape(name, quote=True)}"{" selected" if package_filter == name else ""}>{html.escape(name)}</option>'
        for name in package_names
    )
    action = html.escape(link_prefix.rstrip("/"), quote=True)
    return (
        f'<form class="filters" method="get" action="{action}">'
        f'<label>Severity<select name="severity">{severity_options}</select></label>'
        f'<label>Package<select name="package">{package_options}</select></label>'
        '<button type="submit">Apply filters</button>'
        f'<a href="{action}">Reset</a></form>'
    )


def result_body(
    data: dict[str, Any],
    link_prefix: str = "",
    *,
    severity_filter: str = "all",
    package_filter: str = "",
) -> str:
    summary = data.get("summary") or {}
    severity_order = {"error": 0, "warning": 1, "info": 2}
    if severity_filter not in {"all", "error", "warning", "info"}:
        severity_filter = "all"
    package_names = {str(report["package"]["name"]) for report in data.get("packages", [])}
    if package_filter not in package_names:
        package_filter = ""
    diagnostics = sorted(
        data.get("diagnostics", []),
        key=lambda item: (severity_order.get(str(item.get("severity")), 3), -float(item.get("confidence", 0)), str(item.get("code") or "")),
    )
    diagnostics = [
        item
        for item in diagnostics
        if (severity_filter == "all" or item.get("severity") == severity_filter)
        and (not package_filter or package_filter in diagnostic_packages(item, data))
    ]
    shown = diagnostics[:60]
    findings = "".join(diagnostic_card(item) for item in shown)
    if not findings:
        findings = '<div class="panel"><h3>No static findings</h3><p>No diagnostics remained after the configured policy. Runtime verification may still reveal issues.</p></div>'
    if len(diagnostics) > len(shown):
        findings += f'<p class="muted">Showing the 60 highest-priority findings; {len(diagnostics) - len(shown)} additional findings remain in JSON.</p>'
    downloads = "".join(
        f'<a href="{html.escape(link_prefix + filename, quote=True)}">{html.escape(label)}</a>'
        for filename, label in (("result.json", "JSON"), ("basic.md", "Basic report"), ("intermediate.md", "Intermediate report"), ("expert.md", "Expert report"))
    )
    return f"""
<section><h2>Scan Summary</h2>{metric_cards(summary)}<p class="muted">Static analysis only; runtime/build confirmation remains a separate verification phase.</p></section>
<section><h2>Architecture</h2>{architecture_visual(data)}
<details open><summary>Nodes and communication ownership</summary><table><thead><tr><th>Node</th><th>Package</th><th>Namespace</th><th>Origin</th><th>Endpoints</th></tr></thead><tbody>{node_table(data)}</tbody></table></details>
<details><summary>Topics</summary><table><thead><tr><th>Name</th><th>Types</th><th>Publishers</th><th>Subscribers</th></tr></thead><tbody>{interface_table(data, 'topics', 'publishers', 'subscribers')}</tbody></table></details>
<details><summary>Services</summary><table><thead><tr><th>Name</th><th>Types</th><th>Servers</th><th>Clients</th></tr></thead><tbody>{interface_table(data, 'services', 'servers', 'clients')}</tbody></table></details>
<details><summary>Actions</summary><table><thead><tr><th>Name</th><th>Types</th><th>Servers</th><th>Clients</th></tr></thead><tbody>{interface_table(data, 'actions', 'servers', 'clients')}</tbody></table></details>
<details open><summary>Sensors, algorithms, and actuation</summary><table><thead><tr><th>Category</th><th>Name</th><th>Type</th><th>Role</th><th>Package</th><th>Source</th></tr></thead><tbody>{role_table(data)}</tbody></table></details>
<details open><summary>Modification points</summary><table><thead><tr><th>Task</th><th>Package</th><th>Path</th><th>Why</th></tr></thead><tbody>{modification_table(data)}</tbody></table></details></section>
<section><h2>Prioritized Findings</h2>{findings_filter_form(data, link_prefix, severity_filter, package_filter)}<p class="muted">Showing {len(shown)} of {len(diagnostics)} findings matching the current filters. Informational findings are collapsed by default.</p>{findings}</section>
<section><h2>Reproducibility</h2><table>{provenance_table(data)}</table></section>
<section><h2>Downloads</h2><p class="downloads">{downloads}</p></section>"""


def home_page(csrf_token: str, error: str | None = None) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return page(
        "Robot Doctor",
        f"""<p>Upload a ROS 2 repository ZIP or provide an HTTPS Git URL, then press <strong>Scan</strong>.</p>{error_html}
<form action="/scan" method="post" enctype="multipart/form-data">
<input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
<fieldset><legend>Repository</legend><label>Git URL<br><input type="url" name="git_url" placeholder="https://github.com/owner/repository.git"></label><p class="muted">or</p><label>Repository ZIP<br><input type="file" name="repository_zip" accept=".zip,application/zip"></label></fieldset>
<details><summary>Advanced options</summary><fieldset><legend>Private access and noise controls</legend><label>Private repository token (optional)<br><input type="password" name="git_token" autocomplete="off" placeholder="Fine-grained read-only token"></label><p class="muted">The token is passed only to the clone process, is never written to a task or report, and should have read-only repository access.</p><label>Dependency diagnostics <select name="dependency_mode"><option value="direct">Direct references (recommended)</option><option value="off">Off</option><option value="all">All inferred references</option></select></label><br><label>Suppress codes<br><input type="text" name="suppress_codes" placeholder="RD101,RD202"></label></fieldset></details>
<button type="submit">Scan repository</button></form>
<p class="muted">Runs locally on this computer. Results are temporary and disappear when this application closes. ZIP uploads are size-limited and safely extracted. Dynamic ROS runtime behavior still requires optional runtime verification.</p>""",
    )


def task_page(task: ScanTask, csrf_token: str, *, severity_filter: str = "all", package_filter: str = "") -> str:
    progress = task.progress
    total = progress.get("total") or 1
    percent = min(100, int(100 * progress.get("current", 0) / total))
    body = f'<p><strong>Status:</strong> {html.escape(task.status)}</p><p>{html.escape(progress.get("message") or "")}</p><progress max="100" value="{percent}">{percent}%</progress>'
    refresh = 2 if task.status in {"queued", "intake", "scanning"} else None
    if refresh:
        body += (
            f'<form action="/tasks/{task.id}/cancel" method="post">'
            f'<input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">'
            '<button type="submit">Cancel</button></form>'
        )
    elif task.status == "complete":
        try:
            data = json.loads((task.directory / "result.json").read_text(encoding="utf-8"))
            body += result_body(
                data,
                f"/tasks/{task.id}/",
                severity_filter=severity_filter,
                package_filter=package_filter,
            )
        except (OSError, json.JSONDecodeError) as exc:
            body += f'<p class="error">The rendered result could not be loaded: {html.escape(str(exc))}</p>'
    elif task.error:
        body += f'<p class="error">{html.escape(task.error)}</p>'
    body += '<p><a href="/">Start another scan</a></p>'
    return page(f"Scan {task.id[:8]}", body, refresh=refresh)


def parse_multipart(content_type: str, payload: bytes) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", errors="replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + payload
    )
    fields: dict[str, str] = {}
    upload = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        value = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            upload = (filename, value)
        else:
            fields[name] = value.decode(part.get_content_charset() or "utf-8", errors="replace")
    return fields, upload


def is_loopback_host(host: str) -> bool:
    value = host.strip().strip("[]").rstrip(".").lower()
    if value in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def valid_loopback_host_header(host_header: str | None) -> bool:
    if not host_header:
        return False
    try:
        parsed = urlparse(f"//{host_header}")
        parsed.port
    except ValueError:
        return False
    return bool(parsed.hostname and is_loopback_host(parsed.hostname) and not parsed.username and not parsed.password)


def valid_origin(origin: str | None, host_header: str | None) -> bool:
    if not origin:
        return True
    if origin == "null":
        return valid_loopback_host_header(host_header)
    if not host_header:
        return False
    parsed = urlparse(origin)
    try:
        host = urlparse(f"//{host_header}")
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host_port = host.port or 80
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and bool(parsed.hostname and host.hostname)
        and is_loopback_host(parsed.hostname or "")
        and is_loopback_host(host.hostname or "")
        and origin_port == host_port
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


class RequestHandler(BaseHTTPRequestHandler):
    server: "RobotDoctorHTTPServer"

    def do_GET(self) -> None:
        if not valid_loopback_host_header(self.headers.get("Host")):
            self.send_error(HTTPStatus.FORBIDDEN, "loopback Host header required")
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(home_page(self.server.application.csrf_token))
            return
        if parsed.path == "/healthz":
            self._bytes(b"ok\n", "text/plain; charset=utf-8")
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "tasks":
            task = self.server.application.tasks.get(parts[1])
            if not task:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 2:
                query = parse_qs(parsed.query)
                self._html(
                    task_page(
                        task,
                        self.server.application.csrf_token,
                        severity_filter=(query.get("severity") or ["all"])[0],
                        package_filter=(query.get("package") or [""])[0],
                    )
                )
                return
            filename = parts[2]
            if filename not in {"result.html", "result.json", "basic.md", "intermediate.md", "expert.md"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            path = task.directory / filename
            if task.status != "complete" or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if filename.endswith(".json"):
                content_type = "application/json"
            elif filename.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            else:
                content_type = "text/markdown; charset=utf-8"
            self._bytes(path.read_bytes(), content_type, attachment=None if filename.endswith(".html") else filename)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not valid_loopback_host_header(self.headers.get("Host")):
            self.send_error(HTTPStatus.FORBIDDEN, "loopback Host header required")
            return
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel":
            try:
                fields = self._read_urlencoded_form(16_384)
                self._validate_post(fields)
                task = self.server.application.tasks.get(parts[1])
                if not task:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                task.cancel_event.set()
                self._redirect(f"/tasks/{task.id}")
            except (IntakeError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path != "/scan":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD_BYTES + 1_000_000:
                raise IntakeError("request is empty or exceeds the upload limit")
            payload = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                fields, upload = parse_multipart(content_type, payload)
            else:
                fields = {key: values[0] for key, values in parse_qs(payload.decode("utf-8")).items()}
                upload = None
            self._validate_post(fields)
            suppressions = frozenset(code.strip() for code in fields.get("suppress_codes", "").split(",") if code.strip())
            config = ScanConfig(dependency_mode=fields.get("dependency_mode", "direct"), suppress_diagnostics=suppressions)
            config.validate()
            if upload and upload[1]:
                task = self.server.application.submit_upload(upload[0], upload[1], config)
            elif fields.get("git_url", "").strip():
                task = self.server.application.submit_git(
                    fields["git_url"].strip(),
                    config,
                    access_token=fields.get("git_token") or None,
                )
            else:
                raise IntakeError("provide either a Git URL or a ZIP archive")
            self._redirect(f"/tasks/{task.id}")
        except (IntakeError, ConfigError, ValueError) as exc:
            self._html(home_page(self.server.application.csrf_token, str(exc)), status=HTTPStatus.BAD_REQUEST)

    def _read_urlencoded_form(self, max_bytes: int) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            raise IntakeError("request is empty or exceeds the form limit")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise IntakeError("expected a URL-encoded form")
        return {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode("utf-8")).items()}

    def _validate_post(self, fields: dict[str, str]) -> None:
        if not valid_origin(self.headers.get("Origin"), self.headers.get("Host")):
            raise IntakeError("cross-origin requests are not accepted")
        supplied_token = fields.get("csrf_token", "")
        if not supplied_token or not hmac.compare_digest(supplied_token, self.server.application.csrf_token):
            raise IntakeError("invalid CSRF token")

    def log_message(self, format: str, *args: Any) -> None:
        print(f"web: {format % args}")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._bytes(content.encode("utf-8"), "text/html; charset=utf-8", status=status)

    def _bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, attachment: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(payload)


class RobotDoctorHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], application: WebApplication) -> None:
        self.application = application
        try:
            self.address_family = socket.AF_INET6 if ipaddress.ip_address(address[0]).version == 6 else socket.AF_INET
        except ValueError:
            self.address_family = socket.AF_INET
        super().__init__(address, RequestHandler)


def allowed_bind_host(host: str) -> bool:
    if is_loopback_host(host):
        return True
    return os.environ.get("ROBOT_DOCTOR_CONTAINER") == "1" and host in {"0.0.0.0", "::"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Robot Doctor self-service web interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--max-concurrent-tasks", type=int, default=DEFAULT_MAX_CONCURRENT_TASKS)
    parser.add_argument("--max-checkout-size-mb", type=float, default=DEFAULT_MAX_CHECKOUT_BYTES / (1024 * 1024))
    args = parser.parse_args()
    if not allowed_bind_host(args.host):
        parser.error("--host must be loopback unless ROBOT_DOCTOR_CONTAINER=1 explicitly allows a container wildcard bind")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.max_concurrent_tasks <= 0:
        parser.error("--max-concurrent-tasks must be positive")
    if args.max_checkout_size_mb <= 0:
        parser.error("--max-checkout-size-mb must be positive")
    application = WebApplication(
        max_concurrent_tasks=args.max_concurrent_tasks,
        max_checkout_bytes=int(args.max_checkout_size_mb * 1024 * 1024),
    )
    server = RobotDoctorHTTPServer((args.host, args.port), application)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Robot Doctor web interface: {url}")
    if not args.no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
