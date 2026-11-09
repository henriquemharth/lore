"""Explicit mentions in the text: decisions, commits, migrations and referenced files.

Only what is WRITTEN in the document is recognized, with the line as evidence:
- a decision code (``DEC-001``, ``ADR-0001`` or the patterns configured in sources.yaml) anywhere outside a
  code block → decision;
- a hexadecimal identifier in backticks → commit (7 to 11 or 40 characters, git abbreviations; stored by its
  first 7) or migration (12 characters on a line that talks about migrations/revisions — the shape of
  Alembic revision ids; context words in English and Portuguese, e.g. migration, alembic, revision, head,
  rails, django, flyway); a 12-character one without context stays a plain "hash";
- a file path in backticks, with a folder and a code/config extension → file (a ``.md`` that exists among
  the sources becomes a link to that document).

Nothing is inferred from topic similarity: no text on the line, no mention.
"""

import re
from dataclasses import dataclass

from lore.parser import ParsedDocument
from lore.sources import DecisionRules

_CODE = re.compile(r"(`+)(.+?)\1")
_HEX = re.compile(r"^[0-9a-f]{7,40}$")
_PATH = re.compile(r"^(?:\./)?((?:[\w.@-]+/)+[\w.@-]+\.([A-Za-z0-9]{1,8}))(?::\d+(?:-\d+)?)?[.,;)]?$")
_MIGRATION_CONTEXT = re.compile(
    r"migra|alembic|down_revision|revision|revis[aã]o|cabe[cç]a|\bhead\b|rails|django|flyway|liquibase",
    re.IGNORECASE,
)
_COMMIT_CONTEXT = re.compile(r"\bcommit", re.IGNORECASE)
FILE_EXTENSIONS = {
    "py", "ts", "tsx", "js", "mjs", "cjs", "sh", "css", "json", "yml", "yaml", "toml", "ini",
    "sql", "service", "timer", "caddy", "html", "md", "rb", "go", "rs", "java", "kt", "cs", "php",
}
EXCERPT_LIMIT = 300


@dataclass(frozen=True)
class Mention:
    type: str  # decision | commit | migration | hash | file
    key: str  # DEC-042 | 60041bb | b7d9f1a3c5e8 | backend/app/permissions.py
    line: int  # 1-based
    excerpt: str  # the document line (truncated)
    rule: str  # how it was recognized


def extract(doc: ParsedDocument, rules: DecisionRules | None = None) -> list[Mention]:
    rules = rules or DecisionRules()
    in_block: set[int] = set()
    for a, b in doc.code_blocks:
        in_block.update(range(a, b + 1))

    mentions: dict[tuple[str, str, int], Mention] = {}

    def keep(m: Mention) -> None:
        mentions.setdefault((m.type, m.key, m.line), m)

    for index, line in enumerate(doc.lines):
        if index in in_block or not line.strip():
            continue
        number = index + 1
        excerpt = line.strip()[:EXCERPT_LIMIT]
        for code in rules.find_in_text(line):
            keep(Mention("decision", code, number, excerpt, f"code {code} written in the text"))
        for found in _CODE.finditer(line):
            content = found.group(2).strip()
            hexa = _hexadecimal(content, line)
            if hexa:
                kind, key, reason = hexa
                keep(Mention(kind, key, number, excerpt, reason))
                continue
            path = _path(content)
            if path:
                keep(Mention("file", path, number, excerpt, f"path `{path}` quoted as code"))
    return sorted(mentions.values(), key=lambda m: (m.line, m.type, m.key))


def _hexadecimal(content: str, line: str) -> tuple[str, str, str] | None:
    if not _HEX.match(content) or not re.search(r"\d", content) or not re.search(r"[a-f]", content):
        return None
    size = len(content)
    if size == 12:
        if _MIGRATION_CONTEXT.search(line):
            return "migration", content, f"revision `{content}` as code on a line that talks about migrations"
        if _COMMIT_CONTEXT.search(line):
            return "commit", content[:7], f"hash `{content}` as code on a line that talks about a commit"
        return "hash", content, f"identifier `{content}` as code (no commit or migration context)"
    if 7 <= size <= 11 or size == 40:
        return "commit", content[:7], f"hash `{content}` as code ({size} characters, git commit format)"
    return None


def _path(content: str) -> str | None:
    if content.startswith(("/", "~", "http:", "https:")) or " " in content:
        return None
    found = _PATH.match(content)
    if not found or found.group(2).lower() not in FILE_EXTENSIONS:
        return None
    return found.group(1)
