#!/usr/bin/env python3
"""Validate generated MkDocs HTML for offline ZIP distribution."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


IGNORED_SCHEMES = {
    "data",
    "http",
    "https",
    "javascript",
    "mailto",
    "tel",
}


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value.strip())


def validate_links(site_root: Path) -> list[str]:
    errors: list[str] = []
    site_root = site_root.resolve()

    if not site_root.exists():
        return [f"{site_root}: site directory does not exist"]

    for html_file in sorted(site_root.rglob("*.html")):
        content = html_file.read_text(encoding="utf-8", errors="replace")
        parser = AnchorParser()
        parser.feed(content)

        for href in parser.hrefs:
            if not href or href.startswith("#"):
                continue

            parsed = urlparse(href)
            if parsed.scheme in IGNORED_SCHEMES:
                continue

            link_path = unquote(parsed.path)
            if not link_path:
                continue

            if link_path.endswith("/"):
                errors.append(f"{html_file}: directory-style link: {href}")
                continue

            if link_path.startswith("/"):
                target = (site_root / link_path.lstrip("/")).resolve()
            else:
                target = (html_file.parent / link_path).resolve()

            try:
                target.relative_to(site_root)
            except ValueError:
                errors.append(f"{html_file}: link escapes site root: {href}")
                continue

            if not target.exists():
                errors.append(f"{html_file}: broken link: {href}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, default=Path("build/site"))
    args = parser.parse_args()

    problems = validate_links(args.site_root)
    if problems:
        print("\n".join(problems))
        raise SystemExit(
            f"Offline documentation validation failed: {len(problems)} problem(s)"
        )

    print("Offline documentation links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
