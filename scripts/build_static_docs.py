#!/usr/bin/env python3
"""Build a self-contained offline HTML site directly from Markdown files.

This builder preserves the source folder structure and adds a static navigation
shell without requiring MkDocs. It is intended for ruleflow-first documentation
where ruleflows, subflows, tasks, and rule packages share Markdown pages.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urldefrag, urlparse

try:
    import markdown
except ImportError as exc:  # pragma: no cover - exercised by users without deps.
    raise SystemExit("Install dependencies first: pip install -r requirements.txt") from exc

from validate_offline_site import validate_links


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DOC_SCHEMES = {"", "file"}


@dataclass
class Page:
    rel_md: Path
    rel_html: Path
    title: str
    content: str
    outgoing: set[Path] = field(default_factory=set)
    kind: str = "page"
    artifact: str | None = None


@dataclass
class Usage:
    root: Path
    target: Path
    path: list[Path]


@dataclass
class NavItem:
    target: Path
    label: str
    kind: str
    children: list["NavItem"] = field(default_factory=list)


def normalize_nav_path(source_root: Path, raw_path: str) -> Path:
    rel = Path(raw_path)
    if rel.is_absolute():
        rel = rel.resolve().relative_to(source_root)
    target = source_root / rel
    if target.is_dir():
        for name in ("index.md", "README.md"):
            if (target / name).exists():
                return rel / name
    if target.exists():
        return rel
    raise SystemExit(f"Navigation path does not exist: {raw_path}")


def load_nav_items(nav_path: Path | None, source_root: Path, pages: dict[Path, Page]) -> list[NavItem] | None:
    if nav_path is None:
        return None
    if not nav_path.exists():
        raise SystemExit(f"Navigation file does not exist: {nav_path}")
    data = json.loads(nav_path.read_text(encoding="utf-8"))
    roots = data.get("roots")
    if not isinstance(roots, list):
        raise SystemExit("Navigation JSON must contain a top-level 'roots' array")

    def item_from_json(raw: dict) -> NavItem:
        if not isinstance(raw, dict):
            raise SystemExit("Navigation entries must be JSON objects")
        raw_path = raw.get("path")
        if not raw_path:
            raise SystemExit("Navigation entries must include 'path'")
        target = normalize_nav_path(source_root, str(raw_path))
        if target not in pages:
            raise SystemExit(f"Navigation path is not a Markdown page: {raw_path}")
        raw_children = raw.get("children", [])
        if not isinstance(raw_children, list):
            raise SystemExit(f"Navigation children must be an array: {raw_path}")
        return NavItem(
            target=target,
            label=str(raw.get("label") or pages[target].title),
            kind=str(raw.get("type") or pages[target].kind),
            children=[item_from_json(child) for child in raw_children],
        )

    return [item_from_json(root) for root in roots]


def titleize(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ").replace(".rfl", "")
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return " ".join(part.capitalize() for part in value.split()) or "Untitled"


def slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def output_for_md(rel_md: Path) -> Path:
    return rel_md.with_suffix(".html")


def rel_href(from_html: Path, to_html: Path) -> str:
    rel = Path(
        *(
            [".."] * len(from_html.parent.parts)
            + list(to_html.parts)
        )
    )
    return quote(rel.as_posix())


def relative_path(from_path: Path, to_path: Path) -> Path:
    return Path(*([".."] * len(from_path.parent.parts) + list(to_path.parts)))


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return re.sub(r"\s+#*$", "", match.group(1)).strip()
    return titleize(Path(fallback).stem if Path(fallback).stem.lower() != "readme" else Path(fallback).parent.name)


def is_ruleflow_page(rel_md: Path, text: str) -> bool:
    name = rel_md.name.lower()
    if rel_md.parts and rel_md.parts[0] == "ruleflows":
        return True
    if name.endswith("-rfl.md") or name.endswith(".rfl.md"):
        return True
    heading = first_heading(text, rel_md.as_posix()).lower()
    return "decision flow" in heading or "ruleflow" in heading


def is_external_href(href: str) -> bool:
    parsed = urlparse(href)
    return parsed.scheme not in LOCAL_DOC_SCHEMES or href.startswith("#")


def resolve_md_link(source_root: Path, from_md: Path, href: str) -> Path | None:
    if is_external_href(href):
        return None
    link, _fragment = urldefrag(href)
    if not link:
        return None
    parsed = urlparse(link)
    if parsed.scheme and parsed.scheme != "file":
        return None
    link_path = Path(unquote(parsed.path))
    if link_path.is_absolute():
        try:
            return link_path.resolve().relative_to(source_root)
        except ValueError:
            return None
    candidate = (source_root / from_md.parent / link_path).resolve()
    try:
        rel = candidate.relative_to(source_root)
    except ValueError:
        return None
    if rel.suffix.lower() == ".md" and (source_root / rel).exists():
        return rel
    if (source_root / rel / "README.md").exists():
        return rel / "README.md"
    if (source_root / rel / "index.md").exists():
        return rel / "index.md"
    return None


def extract_ruleflow_artifact(text: str) -> str | None:
    path_match = re.search(r"(?:Path|Flow file|Flow File|Decision Flow Artifact):\s*`?(ruleproject/[^\s`'\"|)]+\.rfl)`?", text)
    if path_match:
        return path_match.group(1)
    any_match = re.search(r"(ruleproject/[^\s`'\"|)]+\.rfl)", text)
    return any_match.group(1) if any_match else None


def resolve_rule_reference(source_root: Path, ref: str, artifact_map: dict[str, Path] | None = None) -> Path | None:
    ref = ref.strip().strip("`'\"")
    ref = ref.replace("\\", "/")
    if not ref or "ruleproject/" not in ref:
        return None
    ref = ref[ref.index("ruleproject/") :]
    ref_path = Path(ref)
    if ref_path.suffix.lower() == ".rfl" and artifact_map:
        for artifact, rel_md in artifact_map.items():
            if artifact == ref or artifact.endswith(f"/{ref}") or ref.endswith(f"/{artifact}"):
                return rel_md
    candidates: list[Path] = []
    if ref_path.suffix.lower() in {".brl", ".dta", ".trl", ".arl"}:
        candidates.append(ref_path.with_suffix(".md"))
        candidates.append(ref_path.parent / "README.md")
        candidates.append(ref_path.parent / "index.md")
        candidates.append(ref_path.parent / "summary.md")
    elif ref_path.suffix.lower() == ".rfl":
        candidates.append(ref_path.with_suffix(".md"))
        candidates.append(ref_path.parent / "README.md")
        candidates.append(ref_path.parent / "index.md")
        candidates.append(ref_path.parent / "summary.md")
    else:
        candidates.extend(
            [
                ref_path / "README.md",
                ref_path / "index.md",
                ref_path / "summary.md",
                ref_path.with_suffix(".md"),
            ]
        )
    for candidate in candidates:
        if (source_root / candidate).exists():
            return candidate
    return None


def extract_links(source_root: Path, rel_md: Path, text: str, artifact_map: dict[str, Path]) -> set[Path]:
    links: set[Path] = set()
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
        href = match.group(1).strip()
        rel = resolve_md_link(source_root, rel_md, href)
        if rel is not None:
            links.add(rel)
    for match in re.finditer(r"(ruleproject/[^\s`'\"|)]+)", text):
        rel = resolve_rule_reference(source_root, match.group(1), artifact_map)
        if rel is not None:
            links.add(rel)
    return links


def load_pages(source_root: Path) -> dict[Path, Page]:
    pages: dict[Path, Page] = {}
    for md_path in sorted(source_root.rglob("*.md")):
        rel_md = md_path.relative_to(source_root)
        text = read_text(md_path)
        page = Page(
            rel_md=rel_md,
            rel_html=output_for_md(rel_md),
            title=first_heading(text, rel_md.as_posix()),
            content=text,
            kind="ruleflow" if is_ruleflow_page(rel_md, text) else "page",
        )
        if page.kind == "ruleflow":
            page.artifact = extract_ruleflow_artifact(page.content)
        pages[rel_md] = page
    artifact_map: dict[str, Path] = {}
    for rel_md, page in pages.items():
        if page.kind != "ruleflow":
            continue
        artifact = page.artifact
        if artifact:
            artifact_map[artifact] = rel_md
    for rel_md, page in pages.items():
        page.outgoing = extract_links(source_root, rel_md, page.content, artifact_map)
    return pages


def choose_root_pages(pages: dict[Path, Page]) -> list[Path]:
    top_ruleflows = [
        path for path, page in pages.items()
        if path.name.lower() != "index.md" and page.kind == "ruleflow" and page.artifact and Path(page.artifact).parent.name == "rules"
    ]
    if top_ruleflows:
        return sorted(top_ruleflows, key=lambda path: pages[path].title.lower())
    ruleflow_dir_roots = [
        path for path, page in pages.items()
        if path.parts and path.parts[0] == "ruleflows" and path.name.lower() != "index.md" and page.kind == "ruleflow"
    ]
    if ruleflow_dir_roots:
        return sorted(ruleflow_dir_roots, key=lambda path: pages[path].title.lower())
    roots = [path for path, page in pages.items() if page.kind == "ruleflow"]
    if roots:
        return sorted(roots, key=lambda path: pages[path].title.lower())
    top_level = [path for path in pages if len(path.parts) == 1 and path.name.lower() != "index.md"]
    return sorted(top_level or list(pages), key=lambda path: pages[path].title.lower())[:10]


def collect_usages(pages: dict[Path, Page], roots: list[Path]) -> dict[Path, list[Usage]]:
    usages: dict[Path, list[Usage]] = {}
    for root in roots:
        stack: list[tuple[Path, list[Path]]] = [(root, [root])]
        seen_paths: set[tuple[Path, ...]] = set()
        while stack:
            current, path = stack.pop()
            key = tuple(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            if current != root:
                usages.setdefault(current, []).append(Usage(root=root, target=current, path=path))
            for child in sorted(pruned_outgoing(current, pages), key=lambda item: pages.get(item, Page(item, output_for_md(item), item.stem, "")).title):
                if child not in pages or child in path:
                    continue
                stack.append((child, [*path, child]))
    return usages


def collect_usages_from_nav(nav_items: list[NavItem]) -> dict[Path, list[Usage]]:
    usages: dict[Path, list[Usage]] = {}

    def visit(root: Path, item: NavItem, path: list[Path]) -> None:
        if item.target != root:
            usages.setdefault(item.target, []).append(Usage(root=root, target=item.target, path=path))
        for child in item.children:
            visit(root, child, [*path, child.target])

    for item in nav_items:
        visit(item.target, item, [item.target])
    return usages


def child_label(parent: Path, child: Path, pages: dict[Path, Page]) -> str:
    page = pages[child]
    if page.kind == "ruleflow":
        return f"Subflow: {page.title}"
    if child.name.lower() in {"index.md", "readme.md", "summary.md"}:
        return f"Task/Package: {titleize(child.parent.name)}"
    return f"Rule: {page.title}"


def reachable(start: Path, target: Path, pages: dict[Path, Page], seen: set[Path] | None = None) -> bool:
    seen = seen or set()
    if start in seen:
        return False
    seen.add(start)
    for child in pages.get(start, Page(start, output_for_md(start), start.stem, "")).outgoing:
        if child == target:
            return True
        if child in pages and reachable(child, target, pages, seen):
            return True
    return False


def pruned_outgoing(parent: Path, pages: dict[Path, Page]) -> set[Path]:
    children = {
        child for child in pages[parent].outgoing
        if child in pages and child != parent and not (child.parts and child.parts[0] == "ruleflows" and child.name.lower() == "index.md")
    }
    pruned = set(children)
    for child in children:
        for sibling in children:
            if (
                child != sibling
                and pages[child].kind == "ruleflow"
                and pages[sibling].kind == "ruleflow"
                and reachable(sibling, child, pages)
            ):
                pruned.discard(child)
                break
    return pruned


def build_nav_item(root: Path, pages: dict[Path, Page], seen: set[Path] | None = None) -> NavItem:
    seen = seen or set()
    seen.add(root)
    page = pages[root]
    children: list[NavItem] = []
    for child in sorted(pruned_outgoing(root, pages), key=lambda item: (pages[item].kind != "ruleflow", pages[item].title.lower()) if item in pages else (True, item.as_posix())):
        if child not in pages or child in seen:
            continue
        child_item = build_nav_item(child, pages, set(seen))
        child_item.label = child_label(root, child, pages)
        children.append(child_item)
    return NavItem(target=root, label=page.title, kind=page.kind, children=children)


def nav_contains(item: NavItem, current: Path) -> bool:
    return item.target == current or any(nav_contains(child, current) for child in item.children)


def render_nav_item(item: NavItem, pages: dict[Path, Page], current: Path, current_html: Path, depth: int = 0) -> str:
    page = pages[item.target]
    cls = " active" if item.target == current else ""
    href = rel_href(current_html, page.rel_html)
    label = html.escape(item.label)
    if not item.children:
        return f'<li class="depth-{depth}{cls}"><a href="{href}">{label}</a></li>'
    open_attr = " open" if nav_contains(item, current) or depth == 0 else ""
    children = "\n".join(render_nav_item(child, pages, current, current_html, depth + 1) for child in item.children)
    return (
        f'<li class="depth-{depth}{cls}"><details{open_attr}>'
        f'<summary><a href="{href}">{label}</a></summary>'
        f'<ul class="nav-list nested">{children}</ul>'
        "</details></li>"
    )


def nav_targets(item: NavItem) -> set[Path]:
    targets = {item.target}
    for child in item.children:
        targets.update(nav_targets(child))
    return targets


def nav_tree(
    pages: dict[Path, Page],
    current: Path,
    roots: list[Path],
    current_html: Path | None = None,
    explicit_nav: list[NavItem] | None = None,
) -> str:
    current_html = current_html or output_for_md(current)
    root_items = explicit_nav if explicit_nav is not None else [build_nav_item(root, pages) for root in roots]
    used_targets: set[Path] = set()
    for item in root_items:
        used_targets.update(nav_targets(item))
    ruleflow_items = [render_nav_item(item, pages, current, current_html) for item in root_items]
    if explicit_nav is not None:
        return "\n".join(ruleflow_items)
    package_pages = [
        path for path, page in pages.items()
        if page.kind != "ruleflow" and path not in used_targets
    ]
    extras = []
    if package_pages:
        package_links = []
        for path in sorted(package_pages, key=lambda item: pages[item].title.lower()):
            page = pages[path]
            cls = " active" if path == current else ""
            package_links.append(f'<li class="depth-1{cls}"><a href="{rel_href(current_html, page.rel_html)}">{html.escape(page.title)}</a></li>')
        extras.append(
            '<li class="depth-0"><details><summary>Other Documentation</summary>'
            f'<ul class="nav-list nested">{"".join(package_links)}</ul></details></li>'
        )
    return "\n".join([*ruleflow_items, *extras])


def breadcrumbs(current_html: Path, rel_md: Path) -> str:
    parts = ['<a href="' + rel_href(current_html, Path("index.html")) + '">Home</a>']
    accum: list[str] = []
    for part in rel_md.parts[:-1]:
        accum.append(part)
        parts.append(html.escape(part))
    parts.append(html.escape(rel_md.stem if rel_md.stem.lower() != "readme" else rel_md.parent.name))
    return " / ".join(parts)


def pager(pages: dict[Path, Page], rel_md: Path) -> str:
    ordered = sorted(pages, key=lambda path: pages[path].title.lower())
    idx = ordered.index(rel_md)
    current_html = output_for_md(rel_md)
    links = []
    if idx > 0:
        prev_page = pages[ordered[idx - 1]]
        links.append(f'<a href="{rel_href(current_html, prev_page.rel_html)}">Previous: {html.escape(prev_page.title)}</a>')
    links.append(f'<a href="{rel_href(current_html, Path("index.html"))}">All Pages</a>')
    if idx + 1 < len(ordered):
        next_page = pages[ordered[idx + 1]]
        links.append(f'<a href="{rel_href(current_html, next_page.rel_html)}">Next: {html.escape(next_page.title)}</a>')
    return " | ".join(links)


def rewrite_markdown_links(rendered: str, page: Page, pages: dict[Path, Page], source_root: Path, current_html: Path | None = None) -> str:
    current_html = current_html or page.rel_html

    def repl(match: re.Match[str]) -> str:
        href = html.unescape(match.group(1))
        if is_external_href(href):
            return match.group(0)
        rel = resolve_md_link(source_root, page.rel_md, href)
        if rel is None or rel not in pages:
            return match.group(0)
        _base, fragment = urldefrag(href)
        target = rel_href(current_html, pages[rel].rel_html)
        if fragment:
            target += "#" + quote(fragment)
        return f'href="{target}"'

    return re.sub(r'href="([^"]+)"', repl, rendered)


def usage_html(page: Page, usages: dict[Path, list[Usage]], pages: dict[Path, Page]) -> str:
    rows = usages.get(page.rel_md, [])
    if not rows:
        return ""
    body = [
        "<section class=\"usage\">",
        "<h2>Ruleflow Usage</h2>",
        "<table><thead><tr><th>Root Ruleflow</th><th>Usage Path</th><th>Open Context</th></tr></thead><tbody>",
    ]
    seen: set[tuple[Path, ...]] = set()
    for usage in rows:
        key = tuple(usage.path)
        if key in seen:
            continue
        seen.add(key)
        root = pages[usage.root]
        path_text = " -> ".join(pages[item].title for item in usage.path)
        context = context_path(usage)
        body.append(
            "<tr>"
            f"<td>{html.escape(root.title)}</td>"
            f"<td>{html.escape(path_text)}</td>"
            f"<td><a href=\"{rel_href(page.rel_html, context)}\">Open context</a></td>"
            "</tr>"
        )
    body.append("</tbody></table></section>")
    return "\n".join(body)


def folder_cross_links(page: Page, pages: dict[Path, Page], current_html: Path | None = None) -> str:
    current_html = current_html or page.rel_html
    name = page.rel_md.name.lower()
    if name == "index.md":
        readme = page.rel_md.parent / "README.md"
        if readme in pages:
            href = rel_href(current_html, pages[readme].rel_html)
            return (
                '<section class="usage">'
                "<h2>Folder Details</h2>"
                f'<p><a href="{href}">Open README details</a></p>'
                "</section>"
            )
    if name == "readme.md":
        index = page.rel_md.parent / "index.md"
        if index in pages:
            href = rel_href(current_html, pages[index].rel_html)
            return (
                '<section class="usage">'
                "<h2>Folder Index</h2>"
                f'<p><a href="{href}">Back to folder index</a></p>'
                "</section>"
            )
    return ""


def context_path(usage: Usage) -> Path:
    path_slug = slug("__".join(item.as_posix() for item in usage.path))
    return Path("_contexts") / slug(usage.root.as_posix()) / path_slug / usage.target.with_suffix(".html")


def render_markdown(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "toc", "attr_list", "def_list"],
        output_format="html5",
    )


def shell(
    *,
    title: str,
    body: str,
    sidebar: str,
    crumbs: str,
    pager_links: str,
    current_kind: str,
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --border:#d8dee4; --muted:#57606a; --nav:#f6f8fa; --link:#0969da; --text:#24292f; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Arial, sans-serif; color:var(--text); }}
    header {{ height:52px; display:flex; align-items:center; gap:16px; padding:0 18px; border-bottom:1px solid var(--border); background:#fff; position:sticky; top:0; z-index:3; }}
    header strong {{ white-space:nowrap; }}
    #filter {{ margin-left:auto; max-width:320px; width:30vw; min-width:160px; padding:7px 9px; border:1px solid var(--border); }}
    .layout {{ display:grid; grid-template-columns:300px minmax(0,1fr) 240px; min-height:calc(100vh - 52px); }}
    nav {{ background:var(--nav); border-right:1px solid var(--border); padding:14px; overflow:auto; }}
    main {{ padding:20px 30px 48px; max-width:1100px; width:100%; }}
    aside {{ border-left:1px solid var(--border); padding:14px; background:#fff; overflow:auto; }}
    a {{ color:var(--link); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
    .crumbs,.pager,.kind {{ color:var(--muted); font-size:13px; margin:0 0 12px; }}
    .pager {{ padding-bottom:12px; border-bottom:1px solid var(--border); }}
    .nav-list {{ list-style:none; padding:0; margin:10px 0; font-size:14px; }}
    .nav-list li {{ margin:3px 0; line-height:1.3; }}
    .nav-list li.active > a {{ font-weight:bold; color:#1f2328; }}
    .depth-1 {{ padding-left:12px; }} .depth-2 {{ padding-left:24px; }} .depth-3 {{ padding-left:36px; }} .depth-4 {{ padding-left:48px; }}
    table {{ border-collapse:collapse; width:100%; margin:14px 0; }} th,td {{ border:1px solid var(--border); padding:7px 9px; vertical-align:top; }} th {{ background:var(--nav); text-align:left; }}
    code, pre {{ background:#f6f8fa; }} code {{ padding:1px 4px; }} pre {{ padding:12px; overflow:auto; border:1px solid var(--border); }}
    .usage {{ margin-top:28px; padding-top:10px; border-top:2px solid var(--border); }}
    #toc ul {{ list-style:none; padding-left:0; }} #toc li {{ margin:6px 0; font-size:13px; }}
    @media (max-width: 980px) {{ .layout {{ grid-template-columns:1fr; }} nav,aside {{ display:none; }} main {{ padding:16px; }} #filter {{ width:45vw; }} }}
  </style>
</head>
<body>
  <header>
    <strong>Offline Ruleflow Documentation</strong>
    <span class="kind">{html.escape(current_kind)}</span>
    <input id="filter" placeholder="Filter navigation">
  </header>
  <div class="layout">
    <nav>
      <strong>Pages</strong>
      <ul class="nav-list" id="navList">{sidebar}</ul>
    </nav>
    <main>
      <div class="crumbs">{crumbs}</div>
      <div class="pager">{pager_links}</div>
      {body}
    </main>
    <aside>
      <strong>On This Page</strong>
      <div id="toc"></div>
    </aside>
  </div>
  <script>
  (function () {{
    var toc = document.getElementById("toc");
    var list = document.createElement("ul");
    document.querySelectorAll("main h2, main h3").forEach(function (heading, index) {{
      if (!heading.id) heading.id = "section-" + index;
      var item = document.createElement("li");
      item.style.marginLeft = heading.tagName === "H3" ? "12px" : "0";
      var link = document.createElement("a");
      link.href = "#" + heading.id;
      link.textContent = heading.textContent;
      item.appendChild(link);
      list.appendChild(item);
    }});
    toc.appendChild(list);
    document.getElementById("filter").addEventListener("input", function (event) {{
      var q = event.target.value.toLowerCase();
      document.querySelectorAll("#navList li").forEach(function (li) {{
        li.style.display = li.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none";
      }});
    }});
  }}());
  </script>
</body>
</html>"""


def render_page(
    source_root: Path,
    output_root: Path,
    page: Page,
    pages: dict[Path, Page],
    usages: dict[Path, list[Usage]],
    roots: list[Path],
    explicit_nav: list[NavItem] | None,
) -> None:
    rendered = render_markdown(page.content)
    rendered = rewrite_markdown_links(rendered, page, pages, source_root, page.rel_html)
    rendered += folder_cross_links(page, pages, page.rel_html)
    rendered += usage_html(page, usages, pages)
    html_text = shell(
        title=page.title,
        body=rendered,
        sidebar=nav_tree(pages, page.rel_md, roots, page.rel_html, explicit_nav),
        crumbs=breadcrumbs(page.rel_html, page.rel_md),
        pager_links=pager(pages, page.rel_md),
        current_kind=page.kind,
    )
    write(output_root / page.rel_html, html_text)


def render_context_page(
    source_root: Path,
    output_root: Path,
    usage: Usage,
    pages: dict[Path, Page],
    roots: list[Path],
    explicit_nav: list[NavItem] | None,
) -> None:
    page = pages[usage.target]
    current_html = context_path(usage)
    rendered = render_markdown(page.content)
    rendered = rewrite_markdown_links(rendered, page, pages, source_root, current_html)
    rendered += folder_cross_links(page, pages, current_html)
    path_text = " -> ".join(pages[item].title for item in usage.path)
    context_banner = (
        "<section class=\"usage\">"
        "<h2>Ruleflow Context</h2>"
        f"<p><strong>Usage path:</strong> {html.escape(path_text)}</p>"
        f"<p><a href=\"{rel_href(current_html, page.rel_html)}\">Open canonical documentation</a></p>"
        "</section>"
    )
    body = context_banner + rendered
    html_text = shell(
        title=f"{pages[usage.root].title} -> {page.title}",
        body=body,
        sidebar=nav_tree(pages, usage.target, roots, current_html, explicit_nav),
        crumbs=" / ".join(
            [
                f'<a href="{rel_href(current_html, Path("index.html"))}">Home</a>',
                "Ruleflow Context",
                html.escape(path_text),
            ]
        ),
        pager_links=f'<a href="{rel_href(current_html, pages[usage.root].rel_html)}">Back to root ruleflow</a> | <a href="{rel_href(current_html, page.rel_html)}">Canonical page</a>',
        current_kind="context",
    )
    write(output_root / current_html, html_text)


def render_home(output_root: Path, pages: dict[Path, Page], roots: list[Path], explicit_nav: list[NavItem] | None) -> None:
    rows = []
    for root in roots:
        page = pages[root]
        rows.append(f'<li><a href="{quote(page.rel_html.as_posix())}">{html.escape(page.title)}</a></li>')
    all_pages = []
    for page in sorted(pages.values(), key=lambda item: item.title.lower()):
        all_pages.append(f'<li><a href="{quote(page.rel_html.as_posix())}">{html.escape(page.title)}</a> <code>{html.escape(page.rel_md.as_posix())}</code></li>')
    body = (
        "<h1>Offline Ruleflow Documentation</h1>"
        "<h2>Ruleflows</h2><ul>" + "\n".join(rows) + "</ul>"
        "<h2>All Documentation Pages</h2><ul>" + "\n".join(all_pages) + "</ul>"
    )
    html_text = shell(
        title="Offline Ruleflow Documentation",
        body=body,
        sidebar=nav_tree(pages, roots[0], roots, Path("index.html"), explicit_nav) if roots else "",
        crumbs="Home",
        pager_links="All Pages",
        current_kind="home",
    )
    write(output_root / "index.html", html_text)


def copy_non_markdown(source_root: Path, output_root: Path, excluded: set[Path] | None = None) -> None:
    excluded = excluded or set()
    for src in source_root.rglob("*"):
        if not src.is_file() or src.suffix.lower() == ".md":
            continue
        rel = src.relative_to(source_root)
        if rel in excluded:
            continue
        dst = output_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def write_start_here(output_root: Path, site_name: str) -> None:
    write(
        output_root / "START_HERE.html",
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(site_name)}</title>
  <meta http-equiv="refresh" content="0; url=index.html">
</head>
<body>
  <h1>{html.escape(site_name)}</h1>
  <p><a href="index.html">Open the documentation</a></p>
</body>
</html>""",
    )


def package_site(site_root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(site_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(site_root).as_posix())


def build(source: Path, output_root: Path, site_name: str, zip_path: Path | None, nav_path: Path | None = None) -> None:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    pages = load_pages(source)
    if not pages:
        raise SystemExit(f"No Markdown files found under {source}")
    explicit_nav = load_nav_items(nav_path, source, pages)
    roots = [item.target for item in explicit_nav] if explicit_nav is not None else choose_root_pages(pages)
    usages = collect_usages_from_nav(explicit_nav) if explicit_nav is not None else collect_usages(pages, roots)
    excluded = {nav_path.relative_to(source)} if nav_path is not None and nav_path.is_relative_to(source) else set()
    copy_non_markdown(source, output_root, excluded)
    for page in pages.values():
        render_page(source, output_root, page, pages, usages, roots, explicit_nav)
    for rows in usages.values():
        for usage in rows:
            render_context_page(source, output_root, usage, pages, roots, explicit_nav)
    render_home(output_root, pages, roots, explicit_nav)
    write_start_here(output_root, site_name)
    problems = validate_links(output_root)
    if problems:
        print("\n".join(problems))
        raise SystemExit(f"Offline static documentation validation failed: {len(problems)} problem(s)")
    if zip_path is not None:
        package_site(output_root, zip_path)
    print(f"Markdown pages: {len(pages)}")
    print(f"Ruleflow roots: {len(roots)}")
    print(f"Navigation mode: {'explicit' if explicit_nav is not None else 'inferred'}")
    print(f"Ruleflow usage contexts: {sum(len(rows) for rows in usages.values())}")
    print(f"Built static site: {output_root}")
    if zip_path is not None:
        print(f"Packaged ZIP: {zip_path}")
    print("Offline documentation links are valid.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="Folder containing Markdown documentation")
    parser.add_argument("--nav", type=Path, help="Optional nav.json file controlling the left navigation tree")
    parser.add_argument("--site-name", default="Offline Ruleflow Documentation")
    parser.add_argument("--build-dir", type=Path, default=PROJECT_ROOT / "build" / "static-site")
    parser.add_argument("--zip-name", default="offline-ruleflow-documentation.zip")
    parser.add_argument("--no-zip", action="store_true", help="Build and validate the HTML site only; do not create a ZIP")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.exists():
        raise SystemExit(f"Source folder does not exist: {source}")
    zip_path = None if args.no_zip else PROJECT_ROOT / "dist" / args.zip_name
    nav_path = args.nav.resolve() if args.nav else None
    build(source, args.build_dir.resolve(), args.site_name, zip_path, nav_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
