#!/usr/bin/env python3
"""Lint Jinja templates for syntax errors and delimiter collisions.

Two classes of bug have shipped from this repo, both where a CSS/JS brace
sequence was silently read as a Jinja delimiter:

  1. ``{% block scripts %}`` written inside a JS ``//`` comment — Jinja parsed
     it as a real (unclosed) block tag, breaking template compilation -> HTTP 500
     on every page that includes the file.

  2. ``@supports not (min-height:1dvh){#main-content{...}}`` in a <style> block —
     the ``{#`` opened a Jinja comment that ate everything up to the next ``#}``
     (</style>, </head>, <body>). Pages returned 200 but rendered an empty body.

This linter catches both:

  * COMPILE   - every template must parse with jinja2 (catches class 1).
  * STYLE     - no Jinja delimiter ({{ {% {#) may appear inside <style>…</style>
                (CSS never needs them; catches class 2 and any future collision).
  * COMMENT   - ``{#`` must be a real comment open: followed by whitespace or the
                ``-`` trim marker. ``{#main-content`` etc. are flagged anywhere.

Usage:
    python scripts/lint_templates.py            # lint main/template
    python scripts/lint_templates.py <dir>...   # lint specific dirs/files

Exits non-zero if any problem is found, so it can gate a commit or CI run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader
    from jinja2.exceptions import TemplateSyntaxError
except ImportError:  # pragma: no cover
    print("error: jinja2 is not installed (pip install -r requirements.txt)", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE_DIR = REPO_ROOT / "main" / "template"

# <style>…</style> regions, non-greedy, case-insensitive, dotall.
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
# Any Jinja delimiter open.
_DELIM_RE = re.compile(r"\{\{|\{%|\{#")
# A ``{#`` that is NOT a real comment open. Real comments start with whitespace
# or the ``-`` whitespace-trim marker: "{# … #}" or "{#- … -#}".
_BAD_COMMENT_RE = re.compile(r"\{#(?![\s-])")


class Finding:
    __slots__ = ("path", "line", "col", "code", "msg")

    def __init__(self, path: Path, line: int, col: int, code: str, msg: str):
        self.path, self.line, self.col, self.code, self.msg = path, line, col, code, msg

    def __str__(self) -> str:
        rel = self.path.relative_to(REPO_ROOT) if self.path.is_relative_to(REPO_ROOT) else self.path
        return f"{rel}:{self.line}:{self.col}: [{self.code}] {self.msg}"


def _line_col(text: str, idx: int) -> tuple[int, int]:
    line = text.count("\n", 0, idx) + 1
    col = idx - (text.rfind("\n", 0, idx))
    return line, col


def lint_source(path: Path, text: str, env: Environment) -> list[Finding]:
    findings: list[Finding] = []

    # 1. COMPILE — does it parse at all?
    try:
        env.parse(text, name=path.name, filename=str(path))
    except TemplateSyntaxError as e:
        findings.append(Finding(path, e.lineno or 1, 1, "COMPILE", e.message or str(e)))

    # 2. STYLE — no Jinja delimiters inside <style> blocks.
    for sm in _STYLE_RE.finditer(text):
        block = sm.group(1)
        base = sm.start(1)
        for dm in _DELIM_RE.finditer(block):
            line, col = _line_col(text, base + dm.start())
            findings.append(Finding(
                path, line, col, "STYLE",
                f"Jinja delimiter {dm.group()!r} inside <style> — CSS braces like "
                f"'{{#id' or '{{{{' collide with Jinja and silently eat markup.",
            ))

    # 3. COMMENT — {# must be a genuine comment open.
    seen = {(f.line, f.col) for f in findings}
    for cm in _BAD_COMMENT_RE.finditer(text):
        line, col = _line_col(text, cm.start())
        if (line, col) in seen:
            continue
        findings.append(Finding(
            path, line, col, "COMMENT",
            "'{#' not followed by whitespace — reads as a Jinja comment open and "
            "eats everything up to the next '#}'. Use '{# … #}' for comments, or "
            "add a space (e.g. CSS '{ #id').",
        ))

    return findings


def collect_templates(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for t in targets:
        if t.is_dir():
            files.extend(sorted(t.rglob("*.html")))
        elif t.is_file():
            files.append(t)
        else:
            print(f"warning: skipping missing path {t}", file=sys.stderr)
    return files


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [DEFAULT_TEMPLATE_DIR]
    files = collect_templates(targets)
    if not files:
        print("no templates found", file=sys.stderr)
        return 2

    # Loader present so {% extends %}/{% include %} resolve; parse() itself
    # doesn't load them, but this keeps the env faithful to the app's config.
    env = Environment(loader=FileSystemLoader(str(DEFAULT_TEMPLATE_DIR)))

    all_findings: list[Finding] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            print(f"warning: cannot read {f}: {e}", file=sys.stderr)
            continue
        all_findings.extend(lint_source(f, text, env))

    if all_findings:
        for finding in sorted(all_findings, key=lambda x: (str(x.path), x.line, x.col)):
            print(finding)
        print(f"\n{len(all_findings)} problem(s) in {len(files)} template(s).", file=sys.stderr)
        return 1

    print(f"OK — {len(files)} templates clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
