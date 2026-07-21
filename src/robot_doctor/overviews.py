#!/usr/bin/env python3
"""Generate repository-agnostic ROS 2 overviews from Robot Doctor scan data."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Iterable


from . import scanner as ros_repo_discover


def string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(string(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={string(item)}" for key, item in value.items())
    return str(value)


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    materialized = [[string(cell).replace("\n", "<br>").replace("|", "\\|") for cell in row] for row in rows]
    if not materialized:
        return "_None detected._"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in materialized)
    return "\n".join(lines)


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "item"


def location(item: dict[str, Any]) -> str:
    file = item.get("file") or item.get("source_file") or ""
    return f"{file}:{item['line']}" if file and item.get("line") else file


def certainty(item: dict[str, Any]) -> str:
    return f"{item.get('fact_type', 'detected')} {float(item.get('confidence', 0)):.0%}"


def first_evidence(item: dict[str, Any]) -> str:
    evidence_items = item.get("evidence") or []
    if not evidence_items:
        return ""
    entry = evidence_items[0]
    file = entry.get("file") or ""
    line = entry.get("line")
    extractor = entry.get("extractor") or ""
    where = f"{file}:{line}" if line else file
    return f"{where} ({extractor})" if extractor else where


def flatten(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = []
    for report in data["packages"]:
        for item in report.get(key, []):
            result.append({"package": report["package"]["name"], **item})
    return result


def package_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for report in data["packages"]:
        package = report["package"]
        dependencies = sorted({item for values in package["dependencies"].values() for item in values})
        detected = []
        counts = {
            "launch": len(report["launch_files"]),
            "executables": len(report["executables"]),
            "nodes": len(report["node_names"]) + len(report["launched_nodes"]),
            "topics": sum(1 for key in ("publishers", "subscriptions") for item in report[key] if item.get("resolved")),
            "services": sum(1 for key in ("service_servers", "service_clients") for item in report[key] if item.get("resolved")),
            "actions": sum(1 for key in ("action_servers", "action_clients") for item in report[key] if item.get("resolved")),
            "interfaces": len(report["interfaces"]),
        }
        for name, count in counts.items():
            if count:
                detected.append(f"{count} {name}")
        rows.append([package["name"], package["path"], package.get("build_type") or "unspecified", ", ".join(detected) or "metadata only", ", ".join(dependencies[:8]), certainty(package)])
    return rows


def entity_rows(data: dict[str, Any], key: str, include_qos: bool = False) -> list[list[Any]]:
    rows = []
    for item in flatten(data, key):
        row = [item["package"], item.get("name") or "<unresolved>", item.get("type") or "", location(item), certainty(item)]
        if include_qos:
            row.insert(3, string(item.get("qos")))
        rows.append(row)
    return rows


def tree_text(root: Path, max_entries: int = 100) -> str:
    paths = []
    for file in ros_repo_discover.iter_repository_files(root, max_entries=max_entries * 10):
        relative = file.relative_to(root)
        if len(relative.parts) <= 4:
            paths.append(relative.as_posix())
        if len(paths) >= max_entries:
            break
    return "\n".join([root.name + "/"] + [f"  {path}" for path in sorted(paths)] + (["  …"] if len(paths) >= max_entries else []))


def package_diagram(data: dict[str, Any]) -> str:
    lines = ["```mermaid", "flowchart LR"]
    names = {report["package"]["name"] for report in data["packages"]}
    for report in data["packages"]:
        name = report["package"]["name"]
        lines.append(f'  {slug(name)}["{name}"]')
    for report in data["packages"]:
        package = report["package"]
        dependencies = {item for values in package["dependencies"].values() for item in values}
        for dependency in sorted(dependencies & names):
            lines.append(f"  {slug(package['name'])} --> {slug(dependency)}")
    if len(lines) == 2:
        lines.append('  none["No ROS packages detected"]')
    lines.append("```")
    return "\n".join(lines)


def topic_diagram(data: dict[str, Any], limit: int = 16) -> str:
    topics = data["architecture"]["topics"][:limit]
    lines = ["```mermaid", "flowchart LR"]
    for topic in topics:
        topic_id = slug("topic_" + topic["name"])
        lines.append(f'  {topic_id}(("{topic["name"]}"))')
        for index, publisher in enumerate(topic["publishers"][:3]):
            endpoint = slug(f"pub_{topic['name']}_{publisher['package']}_{index}")
            lines.append(f'  {endpoint}["{publisher.get("node_name") or publisher["package"]} publisher"] --> {topic_id}')
        for index, subscriber in enumerate(topic["subscribers"][:3]):
            endpoint = slug(f"sub_{topic['name']}_{subscriber['package']}_{index}")
            lines.append(f'  {topic_id} --> {endpoint}["{subscriber.get("node_name") or subscriber["package"]} subscriber"]')
    if not topics:
        lines.append('  none["No resolved topic endpoints detected"]')
    lines.append("```")
    return "\n".join(lines)


def launch_diagram(data: dict[str, Any]) -> str:
    lines = ["```mermaid", "flowchart TD"]
    for launch in data["launch_graph"]["files"]:
        launch_id = slug("launch_" + launch["file"])
        lines.append(f'  {launch_id}["{launch["file"]}"]')
        for index, action in enumerate(launch["actions"][:12]):
            if action["kind"] not in {"node", "composable_node", "container"}:
                continue
            label = "/".join(part for part in [action.get("package"), action.get("executable")] if part) or action["kind"]
            action_id = slug(f"action_{launch['file']}_{index}")
            lines.append(f'  {launch_id} --> {action_id}["{label}"]')
    for index, edge in enumerate(data["launch_graph"]["edges"]):
        source = slug("launch_" + edge["from"])
        target_value = edge.get("to") or "unresolved include"
        target = slug("include_" + target_value + str(index))
        style = "-.->" if not edge.get("resolved") else "-->"
        lines.append(f'  {source} {style} {target}["{target_value}"]')
    if len(lines) == 2:
        lines.append('  none["No launch files detected"]')
    lines.append("```")
    return "\n".join(lines)


def tf_diagram(data: dict[str, Any], limit: int = 30) -> str:
    transforms = data["architecture"]["tf"]["transforms"][:limit]
    lines = ["```mermaid", "flowchart TD"]
    for transform in transforms:
        lines.append(f'  {slug("frame_" + transform["parent"])}["{transform["parent"]}"] -->|"{transform["joint"]}"| {slug("frame_" + transform["child"])}["{transform["child"]}"]')
    if not transforms:
        lines.append('  none["No static URDF transforms detected"]')
    lines.append("```")
    return "\n".join(lines)


def architecture_diagram(data: dict[str, Any]) -> str:
    architecture = data["architecture"]
    lines = ["```mermaid", "flowchart LR"]
    if architecture["sensors"]:
        lines.append('  sensors["Detected or inferred sensors"] --> topics(("ROS communication"))')
    else:
        lines.append('  inputs["External inputs"] --> topics(("ROS communication"))')
    lines.append('  topics --> software["Nodes and runtime plugins"]')
    if architecture["algorithms"]:
        lines.append('  software --> algorithms["Inferred algorithm roles"]')
    if architecture["actuation"]:
        lines.append('  algorithms --> actuation["Inferred command / actuation interfaces"]')
    else:
        lines.append('  software --> outputs["External outputs"]')
    lines.append("```")
    return "\n".join(lines)


def node_graph_diagram(data: dict[str, Any], limit: int = 35) -> str:
    active_nodes = [item for item in data["architecture"]["nodes"] if item.get("active")]
    lines = ["```mermaid", "flowchart LR"]
    edge_count = 0
    for node in active_nodes:
        node_id = slug(node["id"])
        node_label = (node.get("namespace") or "") + "/" + (node.get("name") or node.get("executable") or "unresolved")
        lines.append(f'  {node_id}["{node_label.replace("//", "/")}\\n{node.get("package") or "external"}"]')
        for key, direction, shape in (
            ("publishers", "out", "topic"),
            ("subscriptions", "in", "topic"),
            ("service_servers", "in", "service"),
            ("service_clients", "out", "service"),
            ("action_servers", "in", "action"),
            ("action_clients", "out", "action"),
        ):
            for endpoint in node.get(key, []):
                if edge_count >= limit or not endpoint.get("name") or not endpoint.get("resolved"):
                    continue
                interface_id = slug(f"{shape}_{endpoint['name']}")
                if shape == "topic":
                    lines.append(f'  {interface_id}(("{endpoint["name"]}"))')
                elif shape == "service":
                    lines.append(f'  {interface_id}{{{{"{endpoint["name"]}"}}}}')
                else:
                    lines.append(f'  {interface_id}[["{endpoint["name"]}"]]')
                lines.append(f"  {node_id} --> {interface_id}" if direction == "out" else f"  {interface_id} --> {node_id}")
                edge_count += 1
    if len(lines) == 2:
        lines.append('  none["No source or launch nodes detected"]')
    lines.append("```")
    return "\n".join(lines)


def node_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for node in data["architecture"]["nodes"]:
        endpoints = sum(1 for key in ("publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients") for item in node.get(key, []) if item.get("resolved"))
        rows.append([node.get("package"), node.get("name") or "<unresolved>", node.get("namespace"), node.get("executable"), node.get("origin"), node.get("active"), endpoints, len([item for item in node.get("parameters", []) if item.get("effective")]), certainty(node)])
    return rows


def interface_graph_rows(data: dict[str, Any], key: str) -> list[list[Any]]:
    rows = []
    for interface in data["architecture"][key]:
        servers = ", ".join(sorted({item.get("node_name") or item.get("node_id") or "<unresolved>" for item in interface["servers"]}))
        clients = ", ".join(sorted({item.get("node_name") or item.get("node_id") or "<unresolved>" for item in interface["clients"]}))
        rows.append([interface["name"], ", ".join(interface["types"]), servers, clients, certainty(interface)])
    return rows


def effective_parameter_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for node in data["architecture"]["nodes"]:
        if not node.get("active"):
            continue
        for item in node.get("parameters", []):
            rows.append([node.get("package"), node.get("name") or "<unresolved>", item.get("name"), item.get("value"), item.get("type"), item.get("source"), item.get("selector"), item.get("precedence_rank"), item.get("effective"), f"{float(item.get('confidence', 0)):.0%}"])
    return rows


def diagnostics_rows(data: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item["severity"],
            item["code"],
            item["title"],
            item["message"],
            item.get("remediation", {}).get("summary", ""),
            item.get("remediation", {}).get("commands", []),
            certainty(item),
            first_evidence(item),
        ]
        for item in data["diagnostics"]
    ]


def architecture_rows(data: dict[str, Any], key: str) -> list[list[Any]]:
    rows = []
    for item in data["architecture"][key]:
        rows.append([item.get("package") or "", item.get("name") or "<unresolved>", item.get("type") or item.get("role") or "", item.get("role") or "", location(item), certainty(item)])
    return rows


def launch_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for launch in data["launch_graph"]["files"]:
        rows.append([launch["package"], launch["file"], "launch file", launch["format"], "", "detected 100%"])
        for action in launch["actions"]:
            detail = "/".join(part for part in [action.get("package"), action.get("executable")] if part) or action.get("value") or ""
            modifiers = []
            if action.get("namespace"):
                modifiers.append(f"ns={action['namespace']}")
            if action.get("condition"):
                modifiers.append(f"if={action['condition']}")
            if action.get("remappings"):
                modifiers.append(f"{len(action['remappings'])} remap(s)")
            if action.get("parameters"):
                modifiers.append(f"{len(action['parameters'])} parameter source(s)")
            rows.append([launch["package"], launch["file"], action["kind"], detail, ", ".join(modifiers), certainty(action)])
        for include in launch["includes"]:
            rows.append([launch["package"], launch["file"], "include", include.get("resolved_path") or include.get("target") or "<unresolved>", string(include.get("arguments")), certainty(include)])
    return rows


def interface_rows(data: dict[str, Any], include_fields: bool = False) -> list[list[Any]]:
    rows = []
    for report in data["packages"]:
        for interface in report["interfaces"]:
            fields = []
            for section in interface["sections"]:
                field_text = ", ".join(f"{field['type']} {field['name']}" for field in section["fields"])
                fields.append(f"{section['name']}: {field_text or 'empty'}")
            row = [report["package"]["name"], interface["kind"], interface["name"], interface["file"], certainty(interface)]
            if include_fields:
                row.insert(4, "; ".join(fields))
            rows.append(row)
    return rows


def parameter_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for report in data["packages"]:
        package = report["package"]["name"]
        for item in report["declared_parameters"]:
            rows.append([package, "declared default", item.get("name") or "<unresolved>", item.get("default"), location(item), certainty(item)])
        for item in report["parameter_overrides"]:
            rows.append([package, "YAML override", item.get("name") or "<unresolved>", item.get("value"), location(item), certainty(item)])
    return rows


def lifecycle_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for report in data["packages"]:
        package = report["package"]["name"]
        for item in report["node_names"]:
            if item.get("lifecycle"):
                rows.append([package, item.get("name") or "<unresolved>", "source", location(item), certainty(item)])
        for item in report["launched_nodes"]:
            if item.get("lifecycle"):
                rows.append([package, item.get("name") or item.get("executable") or "<unresolved>", "launch", location(item), certainty(item)])
    return rows


def qos_rows(data: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for key in ("publishers", "subscriptions"):
        for item in flatten(data, key):
            if item.get("qos") and item.get("resolved"):
                rows.append([item["package"], key[:-1], item.get("name") or "<unresolved>", item.get("type") or "", string(item["qos"]), location(item), certainty(item)])
    return rows


def modification_rows(data: dict[str, Any]) -> list[list[Any]]:
    return [[item["task"], item["package"], item["path"], item["reason"], certainty(item), first_evidence(item)] for item in data["architecture"]["modification_points"]]


def factual_scope(data: dict[str, Any]) -> str:
    if data["package_count"] == 0:
        return "No ROS 2 packages were detected. The document intentionally does not infer a robot architecture from unrelated files."
    return f"Static analysis detected {data['summary']['packages']} package(s), {data['summary']['launch_files']} launch file(s), {data['summary']['nodes']} node declaration(s), and {data['summary']['topics']} resolved topic(s)."


def basic_document(root: Path, data: dict[str, Any]) -> str:
    summary = data["summary"]
    health = [[severity, count] for severity, count in summary["diagnostics"].items()]
    return f"""# {root.name} — Basic ROS 2 Overview

## Scope

{factual_scope(data)} This is a static report: **detected** items come directly from files; **inferred** items are cautious architectural classifications; **diagnostic** items are checks that may require runtime confirmation.

## What Was Found

{md_table(['Item', 'Count'], [[key.replace('_', ' ').title(), value] for key, value in summary.items() if key != 'diagnostics'])}

{md_table(['Diagnostic severity', 'Count'], health)}

## Package Map

{package_diagram(data)}

{md_table(['Package', 'Path', 'Build type', 'Detected contents', 'Dependencies', 'Certainty'], package_rows(data))}

## High-Level Flow

{architecture_diagram(data)}

This flow is an architectural summary, not a proven runtime graph. Component roles are only listed when source evidence supports an inference.

## Main Nodes

{md_table(['Package', 'Node', 'Namespace', 'Executable', 'Origin', 'Active', 'Interfaces', 'Effective parameters', 'Certainty'], node_rows(data)[:15])}

## Sensors And Inputs

{md_table(['Package', 'Name', 'Type', 'Role', 'Location', 'Certainty'], architecture_rows(data, 'sensors')[:12])}

## Control Algorithms And Plugins

{md_table(['Package', 'Component', 'Detected type', 'Inferred role', 'Location', 'Certainty'], architecture_rows(data, 'algorithms')[:12])}

## Commands And Actuation

{md_table(['Package', 'Interface', 'Type', 'Role', 'Location', 'Certainty'], architecture_rows(data, 'actuation')[:12])}

## Where To Make Changes

{md_table(['Task', 'Package', 'Path', 'Why this path', 'Certainty', 'Evidence'], modification_rows(data))}

## Important Findings

{md_table(['Severity', 'Code', 'Finding', 'Meaning', 'Recommended repair', 'Verification commands', 'Certainty', 'Evidence'], diagnostics_rows(data)[:15])}
"""


def intermediate_document(root: Path, data: dict[str, Any]) -> str:
    return f"""# {root.name} — Intermediate ROS 2 Overview

## Interpretation Rules

{factual_scope(data)} In the graphs, rectangles are software/files and circles are communication channels. Solid launch edges are resolved local relationships; dashed edges are unresolved or external.

## Repository Structure

```text
{tree_text(root)}
```

## Packages And Dependencies

{package_diagram(data)}

{md_table(['Package', 'Path', 'Build type', 'Detected contents', 'Dependencies', 'Certainty'], package_rows(data))}

## Launch Graph

{launch_diagram(data)}

{md_table(['Owner', 'Launch file', 'Entry kind', 'Target', 'Arguments/modifiers', 'Certainty'], launch_rows(data))}

## Topic Graph

{topic_diagram(data)}

## Node-Level Runtime Graph

{node_graph_diagram(data)}

{md_table(['Package', 'Node', 'Namespace', 'Executable', 'Origin', 'Active', 'Interfaces', 'Effective parameters', 'Certainty'], node_rows(data))}

### Publishers

{md_table(['Package', 'Topic', 'Message type', 'QoS', 'Location', 'Certainty'], entity_rows(data, 'publishers', include_qos=True))}

### Subscribers

{md_table(['Package', 'Topic', 'Message type', 'QoS', 'Location', 'Certainty'], entity_rows(data, 'subscriptions', include_qos=True))}

## Services And Actions

### Service Servers

{md_table(['Package', 'Service', 'Type', 'Location', 'Certainty'], entity_rows(data, 'service_servers'))}

### Service Clients

{md_table(['Package', 'Service', 'Type', 'Location', 'Certainty'], entity_rows(data, 'service_clients'))}

### Action Servers

{md_table(['Package', 'Action', 'Type', 'Location', 'Certainty'], entity_rows(data, 'action_servers'))}

### Action Clients

{md_table(['Package', 'Action', 'Type', 'Location', 'Certainty'], entity_rows(data, 'action_clients'))}

### Resolved Service Graph

{md_table(['Service', 'Types', 'Servers', 'Clients', 'Certainty'], interface_graph_rows(data, 'services'))}

### Resolved Action Graph

{md_table(['Action', 'Types', 'Servers', 'Clients', 'Certainty'], interface_graph_rows(data, 'actions'))}

## Robot Structure And TF

{tf_diagram(data)}

## Sensors, Algorithms, And Actuation

{md_table(['Package', 'Sensor/input', 'Type', 'Role', 'Location', 'Certainty'], architecture_rows(data, 'sensors'))}

{md_table(['Package', 'Plugin/component', 'Detected type', 'Inferred role', 'Location', 'Certainty'], architecture_rows(data, 'algorithms'))}

{md_table(['Package', 'Command interface', 'Type', 'Role', 'Location', 'Certainty'], architecture_rows(data, 'actuation'))}

## Custom Interfaces

{md_table(['Package', 'Kind', 'Name', 'File', 'Certainty'], interface_rows(data))}

## Modification Guide

{md_table(['Task', 'Package', 'Path', 'Why this path', 'Certainty', 'Evidence'], modification_rows(data))}

## Diagnostics

{md_table(['Severity', 'Code', 'Finding', 'Meaning', 'Recommended repair', 'Verification commands', 'Certainty', 'Evidence'], diagnostics_rows(data))}
"""


def expert_document(root: Path, data: dict[str, Any]) -> str:
    unresolved = []
    for key in ("publishers", "subscriptions", "service_servers", "service_clients", "action_servers", "action_clients", "declared_parameters"):
        unresolved.extend([{"entity_kind": key, **item} for item in flatten(data, key) if not item.get("resolved", True)])
    unresolved_rows = [[item["package"], item["entity_kind"], item.get("name") or "<unresolved>", item.get("type") or "", location(item), certainty(item), first_evidence(item)] for item in unresolved]
    return f"""# {root.name} — Expert ROS 2 Static Analysis

## Analysis Contract

- Schema: `{data['schema_version']}`
- Scanner: `{data['scanner']['name']} {data['scanner']['version']}`
- Mode: `{data['scanner']['mode']}`
- Fact classes: detected, inferred, diagnostic
- Started: `{data['provenance']['started_at']}`
- Completed: `{data['provenance']['completed_at']}`
- Duration: `{data['provenance']['duration_seconds']:.3f}` seconds
- Git commit: `{data['provenance']['git']['commit_sha'] or 'not detected'}`
- Git branch: `{data['provenance']['git']['branch'] or 'detached or not detected'}`
- Input type: `{data['provenance']['input']['source_type']}`
- Archive SHA-256: `{data['provenance']['input']['archive_sha256'] or 'not applicable'}`
- Content SHA-256: `{data['provenance']['input']['content_sha256'] or 'not calculated'}`
- ROS distribution: `{data['provenance']['ros_distribution'] or 'not sourced'}`

{factual_scope(data)} Every inventory item carries evidence and confidence in the JSON output. Unresolved expressions remain visible rather than becoming empty names.

## Complete Package Inventory

{md_table(['Package', 'Path', 'Build type', 'Detected contents', 'Dependencies', 'Certainty'], package_rows(data))}

## Launch Topology

{launch_diagram(data)}

{md_table(['Owner', 'Launch file', 'Entry kind', 'Target', 'Arguments/modifiers', 'Certainty'], launch_rows(data))}

## Communication Graph

{topic_diagram(data, limit=30)}

## Node-Level Architecture

{node_graph_diagram(data, limit=80)}

{md_table(['Package', 'Node', 'Namespace', 'Executable', 'Origin', 'Active', 'Interfaces', 'Effective parameters', 'Certainty'], node_rows(data))}

### Publishers

{md_table(['Package', 'Topic', 'Message type', 'QoS', 'Location', 'Certainty'], entity_rows(data, 'publishers', include_qos=True))}

### Subscribers

{md_table(['Package', 'Topic', 'Message type', 'QoS', 'Location', 'Certainty'], entity_rows(data, 'subscriptions', include_qos=True))}

### Service Servers

{md_table(['Package', 'Service', 'Type', 'Location', 'Certainty'], entity_rows(data, 'service_servers'))}

### Service Clients

{md_table(['Package', 'Service', 'Type', 'Location', 'Certainty'], entity_rows(data, 'service_clients'))}

### Action Servers

{md_table(['Package', 'Action', 'Type', 'Location', 'Certainty'], entity_rows(data, 'action_servers'))}

### Action Clients

{md_table(['Package', 'Action', 'Type', 'Location', 'Certainty'], entity_rows(data, 'action_clients'))}

### Resolved Service Graph

{md_table(['Service', 'Types', 'Servers', 'Clients', 'Certainty'], interface_graph_rows(data, 'services'))}

### Resolved Action Graph

{md_table(['Action', 'Types', 'Servers', 'Clients', 'Certainty'], interface_graph_rows(data, 'actions'))}

## QoS Evidence

{md_table(['Package', 'Endpoint', 'Name', 'Type', 'QoS', 'Location', 'Certainty'], qos_rows(data))}

## Lifecycle Nodes

{md_table(['Package', 'Node', 'Detected in', 'Location', 'Certainty'], lifecycle_rows(data))}

## Parameters: Defaults And Overrides

{md_table(['Package', 'Source', 'Parameter', 'Value', 'Location', 'Certainty'], parameter_rows(data))}

### Launch-Time Precedence By Node

{md_table(['Package', 'Node', 'Parameter', 'Value', 'Type', 'Source', 'Selector', 'Precedence', 'Effective', 'Confidence'], effective_parameter_rows(data))}

## Interface Fields

{md_table(['Package', 'Kind', 'Name', 'File', 'Sections and fields', 'Certainty'], interface_rows(data, include_fields=True))}

## TF / URDF Structure

{tf_diagram(data, limit=60)}

{md_table(['Parent', 'Child', 'Joint', 'Location', 'Certainty'], [[item['parent'], item['child'], item['joint'], location(item), certainty(item)] for item in data['architecture']['tf']['transforms']])}

## Inferred Architecture

### Sensors

{md_table(['Package', 'Name', 'Type', 'Role', 'Location', 'Certainty'], architecture_rows(data, 'sensors'))}

### Algorithms And Plugins

{md_table(['Package', 'Component', 'Detected type', 'Inferred role', 'Location', 'Certainty'], architecture_rows(data, 'algorithms'))}

### Actuation And Commands

{md_table(['Package', 'Interface', 'Type', 'Role', 'Location', 'Certainty'], architecture_rows(data, 'actuation'))}

## Unresolved Static Expressions

{md_table(['Package', 'Entity kind', 'Expression', 'Type', 'Location', 'Certainty', 'Evidence'], unresolved_rows)}

## Diagnostics

{md_table(['Severity', 'Code', 'Finding', 'Meaning', 'Recommended repair', 'Verification commands', 'Certainty', 'Evidence'], diagnostics_rows(data))}

## Limitations

{chr(10).join('- ' + item for item in data['limitations'])}
"""


def write_documents(root: Path, output_dir: Path) -> list[Path]:
    data = ros_repo_discover.scan_repository(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = {
        "project_overview_basic.md": basic_document(root, data),
        "project_overview_intermediate.md": intermediate_document(root, data),
        "project_overview_expert.md": expert_document(root, data),
    }
    written = []
    for filename, content in documents.items():
        path = output_dir / filename
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate basic, intermediate, and expert ROS 2 repository overviews.")
    parser.add_argument("repository", nargs="?", default=".", help="Repository or workspace path")
    parser.add_argument("--output-dir", "-o", default="project_overviews", help="Destination directory")
    args = parser.parse_args()
    root = Path(args.repository).resolve()
    if not root.exists() or not root.is_dir():
        parser.error(f"repository path is not a directory: {root}")
    for path in write_documents(root, Path(args.output_dir).resolve()):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
