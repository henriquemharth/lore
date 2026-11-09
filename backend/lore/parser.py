"""Markdown reading: title, headings, optional frontmatter, list-style metadata and links.

Frontmatter is NOT required: most notes have none, and everything keeps working without it.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

import yaml
from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark")

_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_MD_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_METADATA = re.compile(r"^\s{0,3}[-*+]\s+\*\*(?:([^*\n]{1,40}?):\*\*|([^*\n]{1,40}?)\*\*:)\s*(.*)$")
# Plain '- Status: Accepted' (MADR and many ADR templates): a capitalized key of a few words.
_METADATA_PLAIN = re.compile(r"^\s{0,3}[-*+]\s+([A-Z][A-Za-z][A-Za-z -]{0,28}):\s+(\S.*)$")


@dataclass
class Heading:
    level: int
    text: str
    line: int  # 1-based
    anchor: str


@dataclass
class FoundLink:
    type: str  # wikilink | embed | markdown
    target: str  # as written (without anchor or alias)
    anchor: str | None
    text: str | None
    line: int  # 1-based


@dataclass
class ParsedDocument:
    title: str
    text: str  # content with line breaks normalized to \n
    lines: list[str]
    body_start: int  # index (0-based) of the first line after the frontmatter
    frontmatter: dict[str, Any] | None
    metadata: dict[str, str]
    headings: list[Heading]
    links: list[FoundLink]
    code_blocks: list[tuple[int, int]]  # 0-based inclusive line ranges
    warnings: list[str] = field(default_factory=list)

    @property
    def description(self) -> str | None:
        value = (self.frontmatter or {}).get("description")
        return value if isinstance(value, str) else None


def decode(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-8-sig", errors="replace")
        warnings.append("File is not valid UTF-8; invalid characters were replaced with �.")
    return text.replace("\r\n", "\n").replace("\r", "\n"), warnings


def parse(data: bytes, path: str) -> ParsedDocument:
    text, warnings = decode(data)
    lines = text.split("\n")
    frontmatter, body_start, fm_warning = _frontmatter(lines)
    if fm_warning:
        warnings.append(fm_warning)

    body = "\n".join(lines[body_start:])
    headings, blocks = _structure(body, body_start)
    links = _links(lines, body_start, blocks)
    metadata = _metadata(lines, body_start, headings)

    return ParsedDocument(
        title=_title(frontmatter, headings, path),
        text=text,
        lines=lines,
        body_start=body_start,
        frontmatter=frontmatter,
        metadata=metadata,
        headings=headings,
        links=links,
        code_blocks=blocks,
        warnings=warnings,
    )


def _frontmatter(lines: list[str]) -> tuple[dict[str, Any] | None, int, str | None]:
    if not lines or lines[0].strip() != "---":
        return None, 0, None
    for end in range(1, min(len(lines), 200)):
        if lines[end].strip() in ("---", "..."):
            break
    else:
        return None, 0, None
    block = "\n".join(lines[1:end])
    try:
        value = yaml.safe_load(block) if block.strip() else {}
    except yaml.YAMLError as error:
        first = str(error).splitlines()[0] if str(error) else "syntax error"
        return None, 0, f"Invalid frontmatter, read as plain text: {first}"
    if not isinstance(value, dict):
        return None, 0, None
    return _json_safe(value), end + 1, None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _structure(body: str, offset: int) -> tuple[list[Heading], list[tuple[int, int]]]:
    headings: list[Heading] = []
    blocks: list[tuple[int, int]] = []
    tokens = _MD.parse(body)
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and token.map:
            inline = tokens[i + 1]
            text = _inline_text(inline).strip()
            if text:
                headings.append(
                    Heading(level=int(token.tag[1]), text=text, line=token.map[0] + offset + 1, anchor=anchor(text))
                )
        elif token.type in ("fence", "code_block") and token.map:
            blocks.append((token.map[0] + offset, token.map[1] - 1 + offset))
    return headings, blocks


def _inline_text(token: Any) -> str:
    if not token.children:
        return token.content or ""
    parts = []
    for child in token.children:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def anchor(text: str) -> str:
    base = unicodedata.normalize("NFKC", text).lower()
    base = re.sub(r"[^\w\s-]", "", base)
    return re.sub(r"\s+", "-", base.strip())


def _title(frontmatter: dict[str, Any] | None, headings: list[Heading], path: str) -> str:
    fm = frontmatter or {}
    if isinstance(fm.get("title"), str) and fm["title"].strip():
        return fm["title"].strip()
    for h in headings:
        if h.level == 1:
            return h.text
    if isinstance(fm.get("name"), str) and fm["name"].strip():
        return fm["name"].strip()
    return PurePosixPath(path).stem


def _metadata(lines: list[str], start: int, headings: list[Heading]) -> dict[str, str]:
    """A '- **Key:** value' list right at the top (common in decision records: Date, Status, commit…)."""
    limit = len(lines)
    for h in headings:
        if h.level >= 2:
            limit = h.line - 1
            break
    limit = min(limit, start + 60)
    metadata: dict[str, str] = {}
    current: str | None = None
    for line in lines[start:limit]:
        found = _METADATA.match(line)
        plain = None if found else _METADATA_PLAIN.match(line)
        if found or plain:
            current = (found.group(1) or found.group(2)).strip() if found else plain.group(1).strip()
            if current not in metadata and len(metadata) < 20:
                metadata[current] = (found.group(3) if found else plain.group(2)).strip()
            else:
                current = None
        elif current and line.startswith((" ", "\t")) and line.strip():
            metadata[current] = f"{metadata[current]} {line.strip()}".strip()
        else:
            current = None
    return metadata


def _links(lines: list[str], start: int, blocks: list[tuple[int, int]]) -> list[FoundLink]:
    in_block = set()
    for a, b in blocks:
        in_block.update(range(a, b + 1))
    links: list[FoundLink] = []
    for index in range(start, len(lines)):
        if index in in_block:
            continue
        # Inline code has no links: replace it with spaces, keeping positions.
        line = _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), lines[index])
        for m in _WIKILINK.finditer(line):
            inner = m.group(2)
            target_anchor, _, alias = inner.partition("|")
            target, _, target_heading = target_anchor.partition("#")
            target = target.strip()
            if not target:
                continue  # [[#heading]] points to the document itself
            links.append(
                FoundLink(
                    type="embed" if m.group(1) else "wikilink",
                    target=target,
                    anchor=target_heading.strip().lstrip("^") or None,
                    text=alias.strip() or None,
                    line=index + 1,
                )
            )
        for m in _MD_LINK.finditer(line):
            destination = m.group(3)
            if _SCHEME.match(destination) or destination.startswith("#"):
                continue  # external link or internal anchor
            target_path, _, target_heading = destination.partition("#")
            target_path = unquote(target_path.split("?", 1)[0])
            if not target_path.lower().endswith(".md"):
                continue
            links.append(
                FoundLink(
                    type="markdown",
                    target=target_path,
                    anchor=target_heading or None,
                    text=m.group(2) or None,
                    line=index + 1,
                )
            )
    return links
