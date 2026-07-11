#!/usr/bin/env python3
"""Build and package an offline MkDocs site from ODM knowledge-base Markdown."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

from validate_offline_site import validate_links


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CANDIDATES = [
    PROJECT_ROOT.parent
    / "odm-asr-map-backed-example"
    / "out"
    / "manual-llm-rule-requirements"
    / "knowledge-base",
    PROJECT_ROOT.parent
    / "odm-generic-envelope-example"
    / "out"
    / "manual-llm-rule-requirements"
    / "knowledge-base",
]


def titleize(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return " ".join(part.capitalize() for part in value.split()) or "Untitled"


def slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "documentation"


def q(value: str) -> str:
    return json.dumps(value)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def copy_source_markdown(source: Path, docs_dir: Path) -> list[Path]:
    copied: list[Path] = []
    for src in sorted(source.rglob("*.md")):
        rel_path = src.relative_to(source)
        dst = docs_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        copied.append(dst)
    return copied


def load_manifest(source: Path) -> dict:
    manifest = source / "manifest.json"
    if not manifest.exists():
        return {"pages": []}
    return json.loads(manifest.read_text(encoding="utf-8"))


def page_rel(source: Path, raw_page: str) -> Path | None:
    raw = Path(raw_page)
    if raw.is_absolute():
        try:
            return raw.relative_to(source)
        except ValueError:
            return None

    parts = raw.parts
    marker = ("knowledge-base",)
    if "knowledge-base" in parts:
        idx = parts.index("knowledge-base")
        return Path(*parts[idx + 1 :])

    candidate = source / raw
    if candidate.exists():
        return raw

    for parent in [source, *source.parents]:
        candidate = parent / raw
        if candidate.exists():
            try:
                return candidate.relative_to(source)
            except ValueError:
                return None
    return raw


def md_link(from_file: Path, to_file: Path) -> str:
    rel = to_file.relative_to(from_file.parent) if to_file.is_relative_to(from_file.parent) else None
    if rel is None:
        rel = Path(*([".."] * (len(from_file.parent.parts)))) / to_file
    return quote(rel.as_posix())


def relative_link(from_file: Path, to_file: Path) -> str:
    rel = Path(
        *(
            [".."] * len(from_file.parent.parts)
            + list(to_file.parts)
        )
    )
    return quote(rel.as_posix())


def load_ruleflow_index(ruleflow_index: Path | None) -> dict | None:
    if ruleflow_index is None:
        return None
    if not ruleflow_index.exists():
        raise SystemExit(f"Ruleflow index does not exist: {ruleflow_index}")
    return json.loads(ruleflow_index.read_text(encoding="utf-8"))


def load_dependency_graph(dependency_graph: Path | None) -> dict | None:
    if dependency_graph is None:
        return None
    if not dependency_graph.exists():
        raise SystemExit(f"Dependency graph does not exist: {dependency_graph}")
    return json.loads(dependency_graph.read_text(encoding="utf-8"))


def path_matches(left: str, right: str) -> bool:
    return left == right or left.endswith(f"/{right}") or right.endswith(f"/{left}")


def build_graph_lookup(dependency_graph: dict | None) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    if not dependency_graph:
        return lookup
    for node in dependency_graph.get("nodes", []):
        source = node.get("sourceRuleFile")
        if source:
            lookup[source] = node
    return lookup


def find_graph_node(graph_by_file: dict[str, dict], source_file: str) -> dict | None:
    if source_file in graph_by_file:
        return graph_by_file[source_file]
    for candidate, node in graph_by_file.items():
        if path_matches(candidate, source_file):
            return node
    return None


def source_to_doc(source_file: str) -> Path:
    return Path(source_file).with_suffix(".md")


def doc_ref_to_rel(source: Path, doc_ref: str) -> Path | None:
    if not doc_ref:
        return None
    rel = page_rel(source, doc_ref)
    if rel is None:
        return None
    if rel.suffix == ".json":
        rel = rel.with_suffix(".md")
    return rel


def resolve_rule_doc(source: Path, docs_dir: Path, rule_file: str, doc_refs: list[str]) -> Path | None:
    candidates: list[Path] = []
    for ref in doc_refs:
        rel = doc_ref_to_rel(source, ref)
        if rel is not None:
            candidates.append(rel)
    candidates.append(source_to_doc(rule_file))

    for rel in candidates:
        if (docs_dir / rel).exists():
            return rel
    return None


def rule_label(rule_file: str) -> str:
    return titleize(Path(rule_file).stem)


def ruleflow_target(ruleflow: dict) -> Path:
    return Path("ruleflows") / f"{slug(ruleflow.get('path') or ruleflow.get('name') or 'ruleflow')}.md"


def rules_for_task(source: Path, docs_dir: Path, task: dict, graph_by_file: dict[str, dict] | None = None) -> list[dict]:
    rules: list[dict] = []
    seen: set[str] = set()
    for pkg in task.get("packages", []):
        package_docs = pkg.get("ruleDocs", [])
        for idx, source_file in enumerate(pkg.get("sourceFiles", [])):
            if source_file in seen:
                continue
            seen.add(source_file)
            doc_refs = []
            if idx < len(package_docs):
                doc_refs.append(package_docs[idx])
            doc_refs.extend(task.get("ruleDocs", []))
            node = find_graph_node(graph_by_file or {}, source_file)
            rules.append(
                {
                    "source": source_file,
                    "name": node.get("ruleName") if node else rule_label(source_file),
                    "package": pkg.get("name", ""),
                    "packagePath": pkg.get("path", ""),
                    "doc": resolve_rule_doc(source, docs_dir, source_file, doc_refs),
                    "node": node,
                }
            )
    for source_file in task.get("explicitRuleFiles", []):
        if source_file in seen:
            continue
        seen.add(source_file)
        node = find_graph_node(graph_by_file or {}, source_file)
        rules.append(
            {
                "source": source_file,
                "name": node.get("ruleName") if node else rule_label(source_file),
                "package": "explicit rule",
                "packagePath": "",
                "doc": resolve_rule_doc(source, docs_dir, source_file, task.get("ruleDocs", [])),
                "node": node,
            }
        )
    return rules


def evidence_values(items: list[dict], limit: int = 4) -> list[str]:
    values: list[str] = []
    for item in items or []:
        value = item.get("businessPath") or item.get("canonicalPath") or item.get("businessObject") or item.get("canonicalObject") or item.get("normalizedText") or item.get("sourceExpression")
        if value and value not in values:
            values.append(str(value))
        if len(values) >= limit:
            break
    return values


def joined_values(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def object_values(node: dict | None) -> list[str]:
    if not node:
        return []
    values: list[str] = []
    for label, key in (("creates", "objectCreates"), ("inserts", "objectInserts"), ("requires", "objectRequires")):
        for value in evidence_values(node.get(key, []), 3):
            text = f"{label}: {value}"
            if text not in values:
                values.append(text)
    return values[:4]


def rule_doc_label(rule: dict, current_file: Path) -> str:
    if rule.get("doc") is None:
        return "Detailed doc: missing"
    return f"[Open detailed doc]({relative_link(current_file, rule['doc'])})"


def append_rule_evidence(lines: list[str], rule: dict, current_file: Path, indent: str) -> None:
    node = rule.get("node")
    lines.append(f"{indent}    - {rule_doc_label(rule, current_file)}")
    if not node:
        lines.append(f"{indent}    - Evidence: dependency graph node not available")
        return
    reads = joined_values(evidence_values(node.get("reads", [])))
    writes = joined_values(evidence_values(node.get("writes", [])))
    objects = joined_values(object_values(node))
    lines.append(f"{indent}    - Reads: {reads}")
    lines.append(f"{indent}    - Writes: {writes}")
    lines.append(f"{indent}    - Objects: {objects}")


def rule_table_doc(rule: dict, current_file: Path) -> str:
    if rule.get("doc") is None:
        return "missing"
    return f"[open]({relative_link(current_file, rule['doc'])})"


def rule_table_evidence(rule: dict, key: str) -> str:
    node = rule.get("node")
    if not node:
        return "-"
    if key == "objects":
        return joined_values(object_values(node))
    return joined_values(evidence_values(node.get(key, []), 3))


def write_ruleflow_tree(
    lines: list[str],
    source: Path,
    docs_dir: Path,
    current_file: Path,
    ruleflow: dict,
    ruleflow_by_key: dict[str, dict],
    graph_by_file: dict[str, dict],
    depth: int = 0,
    seen: set[str] | None = None,
) -> None:
    seen = seen or set()
    flow_key = ruleflow.get("path") or ruleflow.get("uuid") or ruleflow.get("name", "ruleflow")
    indent = "  " * depth
    if flow_key in seen:
        lines.append(f"{indent}- Subflow cycle skipped: `{flow_key}`")
        return
    seen.add(flow_key)

    for task in ruleflow.get("tasks", []):
        rules = rules_for_task(source, docs_dir, task, graph_by_file)
        lines.append(f"{indent}- Task: `{task.get('identifier', 'task')}` ({len(rules)} rule file(s))")
        for pkg in task.get("packages", []):
            lines.append(f"{indent}  - Package: `{pkg.get('name', '')}`{f' -> `{pkg.get('path')}`' if pkg.get('path') else ''}")
        for rule in rules:
            label = f"{rule['name']}"
            if rule["doc"] is not None:
                label = f"[{label}]({relative_link(current_file, rule['doc'])})"
            lines.append(f"{indent}  - Rule: {label}  ")
            lines.append(f"{indent}    `{rule['source']}`")
            append_rule_evidence(lines, rule, current_file, indent)

    for subflow in ruleflow.get("subflows", []):
        target = find_subflow_target(subflow, ruleflow_by_key)
        sub_name = subflow.get("name") or subflow.get("path") or subflow.get("uuid") or subflow.get("identifier", "subflow")
        if target is None:
            lines.append(f"{indent}- Subflow: `{subflow.get('identifier', 'subflow')}` -> unresolved `{sub_name}`")
            continue
        target_page = ruleflow_target(target)
        lines.append(f"{indent}- Subflow: [{target.get('name', sub_name)}]({relative_link(current_file, target_page)})")
        write_ruleflow_tree(lines, source, docs_dir, current_file, target, ruleflow_by_key, graph_by_file, depth + 1, set(seen))


def find_subflow_target(subflow: dict, ruleflow_by_key: dict[str, dict]) -> dict | None:
    for key in (subflow.get("path"), subflow.get("uuid"), subflow.get("name")):
        if key and key in ruleflow_by_key:
            return ruleflow_by_key[key]
    return None


def ruleflow_keys(ruleflow: dict) -> list[str]:
    return [str(value) for value in (ruleflow.get("path"), ruleflow.get("uuid"), ruleflow.get("name")) if value]


def build_ruleflow_lookup(ruleflows: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for ruleflow in ruleflows:
        for key in ruleflow_keys(ruleflow):
            lookup[key] = ruleflow
    return lookup


def collect_ruleflow_rule_rows(source: Path, docs_dir: Path, ruleflow: dict, ruleflow_by_key: dict[str, dict], graph_by_file: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    seen_flows: set[str] = set()

    def visit(flow: dict, origin: str) -> None:
        flow_key = flow.get("path") or flow.get("uuid") or flow.get("name", "ruleflow")
        if flow_key in seen_flows:
            return
        seen_flows.add(flow_key)
        for task in flow.get("tasks", []):
            for rule in rules_for_task(source, docs_dir, task, graph_by_file):
                rows.append({**rule, "ruleflow": flow.get("name", "ruleflow"), "ruleflowPath": flow.get("path", ""), "task": task.get("identifier", "task"), "origin": origin})
        for subflow in flow.get("subflows", []):
            target = find_subflow_target(subflow, ruleflow_by_key)
            if target is not None:
                visit(target, f"subflow:{subflow.get('identifier', target.get('name', 'subflow'))}")

    visit(ruleflow, "direct")
    return rows


def ruleflow_nav_links(current_file: Path, previous_flow: dict | None, next_flow: dict | None) -> list[str]:
    links = ["[All Ruleflows](index.md)"]
    if previous_flow is not None:
        links.append(f"[Previous: {previous_flow.get('name', 'ruleflow')}]({relative_link(current_file, ruleflow_target(previous_flow))})")
    if next_flow is not None:
        links.append(f"[Next: {next_flow.get('name', 'ruleflow')}]({relative_link(current_file, ruleflow_target(next_flow))})")
    return [" | ".join(links)]


def append_ruleflow_usage_to_rule_docs(docs_dir: Path, rule_to_flow: dict[str, list[dict]]) -> int:
    updated = 0
    for source_file, rows in sorted(rule_to_flow.items()):
        doc = next((row.get("doc") for row in rows if row.get("doc") is not None), None)
        if doc is None:
            continue
        target = docs_dir / doc
        if not target.exists():
            continue
        content = target.read_text(encoding="utf-8", errors="replace").rstrip()
        marker = "## Ruleflow Usage"
        if marker in content:
            content = content.split(marker, 1)[0].rstrip()
        lines = [
            "",
            marker,
            "",
            "This rule is referenced by the following ruleflow task paths in the generated ruleflow index.",
            "",
            "| Root Ruleflow | Owning Ruleflow | Task | Navigation |",
            "|---|---|---|---|",
        ]
        unique_rows = sorted(
            {(row["rootRuleflow"], row["ruleflow"], row["task"], row["rootPath"]): row for row in rows}.values(),
            key=lambda item: (item["rootRuleflow"], item["ruleflow"], item["task"]),
        )
        for row in unique_rows:
            nav = f"[Open ruleflow]({relative_link(doc, ruleflow_target({'path': row['rootPath'], 'name': row['rootRuleflow']}))})"
            lines.append(f"| `{row['rootRuleflow']}` | `{row['ruleflow']}` | `{row['task']}` | {nav} |")
        target.write_text(content + "\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")
        updated += 1
    return updated


def generate_ruleflow_pages(source: Path, docs_dir: Path, ruleflow_index: dict | None, dependency_graph: dict | None = None) -> dict[str, list[dict]]:
    if not ruleflow_index:
        return {}
    ruleflows = ruleflow_index.get("ruleflows", [])
    if not ruleflows:
        return {}
    ruleflow_by_key = build_ruleflow_lookup(ruleflows)
    graph_by_file = build_graph_lookup(dependency_graph)
    ruleflow_dir = docs_dir / "ruleflows"
    catalog_dir = docs_dir / "catalogs"
    rule_to_flow: dict[str, list[dict]] = {}

    sorted_ruleflows = sorted(ruleflows, key=lambda item: item.get("name", ""))

    index_lines = [
        "# Ruleflows",
        "",
        "Start here to review the ODM application by decision flow. Each ruleflow page shows operations, direct RuleTasks, rules, and nested subflows.",
        "",
        "| Ruleflow | Path | Tasks | Subflows | Rules Including Subflows |",
        "|---|---|---:|---:|---:|",
    ]

    for index, ruleflow in enumerate(sorted_ruleflows):
        target = ruleflow_target(ruleflow)
        previous_flow = sorted_ruleflows[index - 1] if index > 0 else None
        next_flow = sorted_ruleflows[index + 1] if index + 1 < len(sorted_ruleflows) else None
        rows = collect_ruleflow_rule_rows(source, docs_dir, ruleflow, ruleflow_by_key, graph_by_file)
        index_lines.append(
            f"| [{ruleflow.get('name', 'ruleflow')}]({quote(target.name)}) | `{ruleflow.get('path', '')}` | {len(ruleflow.get('tasks', []))} | {len(ruleflow.get('subflows', []))} | {len(rows)} |"
        )
        for row in rows:
            rule_to_flow.setdefault(row["source"], []).append({**row, "rootRuleflow": ruleflow.get("name", "ruleflow"), "rootPath": ruleflow.get("path", "")})

        page_lines = [
            f"# {ruleflow.get('name', 'Ruleflow')}",
            "",
            f"Path: `{ruleflow.get('path', '')}`",
        ]
        if ruleflow.get("uuid"):
            page_lines.append(f"UUID: `{ruleflow.get('uuid')}`")
        page_lines.extend(["", *ruleflow_nav_links(target, previous_flow, next_flow), "", "## Operations", ""])
        operations = ruleflow.get("operations", [])
        if operations:
            for operation in operations:
                page_lines.append(f"- `{operation.get('name', '')}` ({operation.get('relativePath', '')})")
        else:
            page_lines.append("- None listed in the ruleflow index")

        page_lines.extend(
            [
                "",
                "## Ruleflow Task Tree",
                "",
                "This is the static ruleflow structure from ODM ruleflow files. Exact runtime firing order requires an ODM execution trace.",
                "",
            ]
        )
        write_ruleflow_tree(page_lines, source, docs_dir, target, ruleflow, ruleflow_by_key, graph_by_file)

        page_lines.extend(["", "## Direct Tasks", ""])
        for task in ruleflow.get("tasks", []):
            rules = rules_for_task(source, docs_dir, task, graph_by_file)
            page_lines.extend([f"### {task.get('identifier', 'task')}", ""])
            if task.get("packages"):
                page_lines.extend(["Packages:", ""])
                for pkg in task.get("packages", []):
                    page_lines.append(f"- `{pkg.get('name', '')}`{f' -> `{pkg.get('path')}`' if pkg.get('path') else ''}")
                page_lines.append("")
            if rules:
                page_lines.extend(["| Rule | Source | Package | Detailed Doc | Reads | Writes | Objects |", "|---|---|---|---|---|---|---|"])
                for rule in rules:
                    label = rule["name"]
                    if rule["doc"] is not None:
                        label = f"[{label}]({relative_link(target, rule['doc'])})"
                    page_lines.append(f"| {label} | `{rule['source']}` | `{rule['package']}` | {rule_table_doc(rule, target)} | {rule_table_evidence(rule, 'reads')} | {rule_table_evidence(rule, 'writes')} | {rule_table_evidence(rule, 'objects')} |")
                page_lines.append("")
            else:
                page_lines.extend(["No rule files were resolved for this task.", ""])

        if ruleflow.get("subflows"):
            page_lines.extend(["## Direct Subflows", "", "| Subflow Task | Target Ruleflow | Path |", "|---|---|---|"])
            for subflow in ruleflow.get("subflows", []):
                target_flow = find_subflow_target(subflow, ruleflow_by_key)
                if target_flow is None:
                    page_lines.append(f"| `{subflow.get('identifier', '')}` | unresolved | `{subflow.get('path') or subflow.get('uuid') or ''}` |")
                    continue
                target_page = ruleflow_target(target_flow)
                page_lines.append(f"| `{subflow.get('identifier', '')}` | [{target_flow.get('name', '')}]({relative_link(target, target_page)}) | `{target_flow.get('path', '')}` |")
            page_lines.append("")

        page_lines.extend(["", *ruleflow_nav_links(target, previous_flow, next_flow), ""])
        write(docs_dir / target, "\n".join(page_lines))

    write(ruleflow_dir / "index.md", "\n".join(index_lines))
    write_ruleflow_catalogs(catalog_dir, docs_dir, rule_to_flow)
    return rule_to_flow


def write_ruleflow_catalogs(catalog_dir: Path, docs_dir: Path, rule_to_flow: dict[str, list[dict]]) -> None:
    task_lines = [
        "# Ruleflow Task Catalog",
        "",
        "| Root Ruleflow | Owning Ruleflow | Task | Rule | Source |",
        "|---|---|---|---|---|",
    ]
    for source_file in sorted(rule_to_flow):
        for row in sorted(rule_to_flow[source_file], key=lambda item: (item["rootRuleflow"], item["ruleflow"], item["task"])):
            label = row["name"]
            if row["doc"] is not None:
                label = f"[{label}]({relative_link(Path('catalogs/ruleflow-task-catalog.md'), row['doc'])})"
            task_lines.append(f"| `{row['rootRuleflow']}` | `{row['ruleflow']}` | `{row['task']}` | {label} | `{source_file}` |")
    write(catalog_dir / "ruleflow-task-catalog.md", "\n".join(task_lines))

    reverse_lines = [
        "# Rule To Ruleflow Catalog",
        "",
        "| Rule | Source | Referenced From Ruleflows |",
        "|---|---|---|",
    ]
    for source_file in sorted(rule_to_flow):
        rows = rule_to_flow[source_file]
        first = rows[0]
        label = first["name"]
        if first["doc"] is not None:
            label = f"[{label}]({relative_link(Path('catalogs/rule-to-ruleflow-catalog.md'), first['doc'])})"
        flow_names = ", ".join(sorted({f"`{row['rootRuleflow']}`" for row in rows}))
        reverse_lines.append(f"| {label} | `{source_file}` | {flow_names} |")
    write(catalog_dir / "rule-to-ruleflow-catalog.md", "\n".join(reverse_lines))


def write_catalogs(source: Path, docs_dir: Path, manifest: dict, site_name: str, has_ruleflows: bool) -> None:
    pages = manifest.get("pages", [])
    catalog_dir = docs_dir / "catalogs"
    catalog_index = catalog_dir / "index.md"

    by_type: dict[str, list[dict]] = {}
    for entry in pages:
        by_type.setdefault(entry.get("type", "page"), []).append(entry)

    lines = [
        "# Catalogs",
        "",
        "Use these catalogs for offline navigation. Browser search can be limited when opened through `file://`.",
        "",
        "## By Page Type",
        "",
    ]

    for page_type in sorted(by_type):
        target = catalog_dir / f"{slug(page_type)}.md"
        lines.append(f"- [{titleize(page_type)}]({target.name})")
        write_type_catalog(source, docs_dir, target, page_type, by_type[page_type])

    if has_ruleflows:
        lines.extend(
            [
                "",
                "## Ruleflow Navigation",
                "",
                "- [Ruleflows](../ruleflows/index.md)",
                "- [Ruleflow Task Catalog](ruleflow-task-catalog.md)",
                "- [Rule To Ruleflow Catalog](rule-to-ruleflow-catalog.md)",
            ]
        )

    lines.extend(
        [
            "",
            "## Project",
            "",
            f"- Rule project: `{manifest.get('ruleProject', 'Unknown')}`",
            f"- Focus folder: `{manifest.get('focusFolder', 'Unknown')}`",
            f"- Markdown pages: {len(list(source.rglob('*.md')))}",
            f"- Catalog entries: {len(pages)}",
        ]
    )
    write(catalog_index, "\n".join(lines))

    home = [
        f"# {site_name}",
        "",
        "This offline documentation portal was generated from ODM knowledge-base Markdown.",
        "",
        "## Start Here",
        "",
        *( ["- [Ruleflows](ruleflows/index.md)"] if has_ruleflows else [] ),
        "- [Catalogs](catalogs/index.md)",
        "- [Knowledge Base Index](kb-index.md)",
        "- [Generation Summary](generation-summary.md)",
        "",
        "## Offline Use",
        "",
        "Open `START_HERE.html` from the extracted ZIP. All navigation links are generated as explicit `.html` files for direct browser use without a web server.",
    ]
    write(docs_dir / "index.md", "\n".join(home))

    kb_index = [
        "# Knowledge Base Index",
        "",
        "This index lists every Markdown page included in the generated HTML site.",
        "",
        "| Page | Path |",
        "|---|---|",
    ]
    for page in sorted(docs_dir.rglob("*.md")):
        if page.relative_to(docs_dir).as_posix().startswith("catalogs/"):
            continue
        rel_path = page.relative_to(docs_dir)
        kb_index.append(
            f"| [{titleize(page.stem)}]({quote(rel_path.as_posix())}) | `{rel_path.as_posix()}` |"
        )
    write(docs_dir / "kb-index.md", "\n".join(kb_index))


def write_type_catalog(
    source: Path,
    docs_dir: Path,
    target: Path,
    page_type: str,
    entries: list[dict],
) -> None:
    lines = [
        f"# {titleize(page_type)} Catalog",
        "",
        "| Source | Page |",
        "|---|---|",
    ]
    for entry in sorted(entries, key=lambda item: item.get("source", "")):
        rel_page = page_rel(source, entry.get("page", ""))
        if rel_page is None:
            continue
        page = docs_dir / rel_page
        if not page.exists():
            continue
        link = relative_link(target.relative_to(docs_dir), rel_page)
        source_name = entry.get("source", rel_page.as_posix())
        lines.append(f"| `{source_name}` | [page]({link}) |")
    write(target, "\n".join(lines))


def yaml_nav(site_name: str, has_ruleflows: bool) -> str:
    nav_lines = [
        "nav:",
        "  - Home: index.md",
    ]
    if has_ruleflows:
        nav_lines.extend(
            [
                "  - Ruleflows:",
                "      - Overview: ruleflows/index.md",
            ]
        )
    nav_lines.extend(
        [
            "  - Catalogs:",
            "      - Overview: catalogs/index.md",
        ]
    )
    if has_ruleflows:
        nav_lines.extend(
            [
                "      - Ruleflow Task Catalog: catalogs/ruleflow-task-catalog.md",
                "      - Rule To Ruleflow Catalog: catalogs/rule-to-ruleflow-catalog.md",
            ]
        )
    nav_lines.extend(["  - Knowledge Base: kb-index.md", "  - Generation Summary: generation-summary.md"])

    return "\n".join(
        [
            f"site_name: {q(site_name)}",
            "site_description: Business and technical ODM application documentation",
            "docs_dir: docs",
            "site_dir: ../site",
            "use_directory_urls: false",
            "",
            "theme:",
            "  name: material",
            "  features:",
            "    - navigation.tabs",
            "    - navigation.sections",
            "    - navigation.expand",
            "    - navigation.indexes",
            "    - navigation.top",
            "    - navigation.footer",
            "    - toc.follow",
            "    - content.code.copy",
            "",
            "plugins:",
            "  - search",
            "",
            "markdown_extensions:",
            "  - tables",
            "  - admonition",
            "  - attr_list",
            "  - def_list",
            "  - footnotes",
            "  - pymdownx.details",
            "  - pymdownx.superfences",
            "  - toc:",
            "      permalink: true",
            "",
            *nav_lines,
        ]
    )


def build_mkdocs(mkdocs_dir: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--clean"],
        cwd=mkdocs_dir,
        check=True,
    )


def package_site(site_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(site_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(site_dir).as_posix())


def resolve_default_source() -> Path:
    for candidate in DEFAULT_SOURCE_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_SOURCE_CANDIDATES[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=resolve_default_source())
    parser.add_argument("--site-name", default="ODM Application Documentation")
    parser.add_argument("--ruleflow-index", type=Path, help="Optional ruleflow-task-index.json used to generate ruleflow-first navigation")
    parser.add_argument("--dependency-graph", type=Path, help="Optional rule-dependency-graph.json used to enrich ruleflow pages with reads, writes, and objects")
    parser.add_argument("--build-dir", type=Path, default=PROJECT_ROOT / "build")
    parser.add_argument("--zip-name", default="odm-application-documentation.zip")
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.exists():
        raise SystemExit(f"Knowledge-base source does not exist: {source}")

    build_dir = args.build_dir.resolve()
    mkdocs_dir = build_dir / "mkdocs"
    docs_dir = mkdocs_dir / "docs"
    site_dir = build_dir / "site"
    dist_zip = PROJECT_ROOT / "dist" / args.zip_name

    if build_dir.exists():
        shutil.rmtree(build_dir)
    mkdocs_dir.mkdir(parents=True)

    copied = copy_source_markdown(source, docs_dir)
    manifest = load_manifest(source)
    ruleflow_index = load_ruleflow_index(args.ruleflow_index.resolve() if args.ruleflow_index else None)
    dependency_graph = load_dependency_graph(args.dependency_graph.resolve() if args.dependency_graph else None)
    has_ruleflows = bool(ruleflow_index and ruleflow_index.get("ruleflows"))
    rule_to_flow = generate_ruleflow_pages(source, docs_dir, ruleflow_index, dependency_graph)
    usage_pages = append_ruleflow_usage_to_rule_docs(docs_dir, rule_to_flow)
    write_catalogs(source, docs_dir, manifest, args.site_name, has_ruleflows)
    write(mkdocs_dir / "mkdocs.yml", yaml_nav(args.site_name, has_ruleflows))

    build_mkdocs(mkdocs_dir)

    shutil.copyfile(PROJECT_ROOT / "packaging" / "START_HERE.html", site_dir / "START_HERE.html")

    problems = validate_links(site_dir)
    if problems:
        print("\n".join(problems))
        raise SystemExit(
            f"Offline documentation validation failed: {len(problems)} problem(s)"
        )

    package_site(site_dir, dist_zip)

    print(f"Copied Markdown pages: {len(copied)}")
    if has_ruleflows:
        print(f"Generated ruleflow pages: {len(ruleflow_index.get('ruleflows', []))}")
        print(f"Updated knowledge-base rule pages with ruleflow backlinks: {usage_pages}")
    print(f"Built site: {site_dir}")
    print(f"Packaged ZIP: {dist_zip}")
    print("Offline documentation links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
