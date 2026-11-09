"""Memories written by AI agents through MCP (the lore_remember tool).

Rules:
- each memory is a NEW .md file in lore's memories folder (LORE_MEMORIES_DIR); no existing .md is opened for
  writing or changed — not even the ones in that folder. ``forget`` (lore_forget) only MOVES a file that this
  folder generated into ``.trash/``; nothing is deleted;
- the file name is generated here (UTC date-time + summary of the title + start of the content sha256) and
  published with ``os.link``, which fails if the name already exists: the client never chooses a path and
  nothing is overwritten;
- text that looks like a secret is refused entirely (nothing is written);
- the same content does not become a second memory: the existing one is returned;
- the memory starts as "unverified": it is what an AI recorded, not reviewed documentation.
"""

import hashlib
import os
import re
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lore.privacy import Privacy, redact
from lore.sources import DecisionRules, Source, SourcesConfig

KINDS = ("fact", "decision", "preference", "procedure", "problem", "reference")
MAX_TITLE = 120
MIN_CONTENT = 20
MAX_EVIDENCE = 10
MAX_CHARS_PER_EVIDENCE = 300
TRASH = ".trash"  # hidden: the scanner skips it, so the memory leaves the index on the next run
GENERATED_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[a-z0-9-]+-[0-9a-f]{12}\.md$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class MemoryRejected(ValueError):
    """Invalid or unsafe input: nothing was written."""


@dataclass(frozen=True)
class NewMemory:
    title: str
    content: str
    kind: str
    subject: str | None
    evidence: tuple[str, ...]
    decisions: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SavedMemory:
    file: str
    sha256: str
    created: bool  # False: the same content was already saved


def memory_source(sources: SourcesConfig, folder: Path) -> Source | None:
    """The source of sources.yaml that reads the memories folder (without it, the memory would never be indexed)."""
    target = Path(folder).expanduser().resolve()
    return next((s for s in sources.sources if s.type == "folder" and s.root.expanduser().resolve() == target), None)


def validate(
    title: str | None,
    content: str | None,
    kind: str | None,
    subject: str | None,
    evidence: list[str] | None,
    decisions: list[str] | None,
    *,
    max_chars: int,
    rules: DecisionRules | None = None,
) -> NewMemory:
    rules = rules or DecisionRules()
    title = " ".join(_clean(title).split())
    if len(title) < 3 or len(title) > MAX_TITLE or not re.search(r"\w", title):
        raise MemoryRejected(f"'title' must have 3 to {MAX_TITLE} characters, with letters or digits.")
    content = "\n".join(line.rstrip() for line in _clean(content).replace("\r\n", "\n").split("\n")).strip()
    if len(content) < MIN_CONTENT or len(content) > max_chars:
        raise MemoryRejected(f"'content' must have {MIN_CONTENT} to {max_chars} characters.")
    kind = (kind or "fact").strip().lower()
    if kind not in KINDS:
        raise MemoryRejected(f"'kind' must be one of: {', '.join(KINDS)}.")
    subject = " ".join(_clean(subject).split()) or None
    if subject and len(subject) > MAX_TITLE:
        raise MemoryRejected(f"'subject' may have up to {MAX_TITLE} characters.")
    items = [" ".join(_clean(e).split()) for e in evidence or []]
    items = [e for e in items if e]
    if len(items) > MAX_EVIDENCE or any(len(e) > MAX_CHARS_PER_EVIDENCE for e in items):
        raise MemoryRejected(f"'evidence' accepts up to {MAX_EVIDENCE} items of up to {MAX_CHARS_PER_EVIDENCE} characters.")
    codes = [str(d).strip().upper() for d in decisions or [] if str(d).strip()]
    invalid = [d for d in codes if not rules.is_code(d)]
    if invalid or len(codes) > MAX_EVIDENCE:
        raise MemoryRejected(f"'related_decisions' only accepts decision codes such as DEC-031 or ADR-0007 (up to {MAX_EVIDENCE}).")

    for field_name, text in (("title", title), ("content", content), ("subject", subject), ("evidence", "\n".join(items))):
        if redact(text)[1]:
            raise MemoryRejected(
                f"'{field_name}' seems to contain a secret (password, token, key). Nothing was written: record where "
                "the secret lives, never its value."
            )
    return NewMemory(
        title=title,
        content=content,
        kind=kind,
        subject=subject,
        evidence=tuple(dict.fromkeys(items)),
        decisions=tuple(dict.fromkeys(codes)),
    )


def existing(folder: Path, sha256: str) -> str | None:
    """Name of the file that already holds this content (the name suffix is the start of the sha256)."""
    if not folder.is_dir():
        return None
    return next((p.name for p in sorted(folder.glob(f"*-{sha256[:12]}.md")) if p.is_file()), None)


def save(
    folder: Path,
    memory: NewMemory,
    *,
    author: str | None,
    source: Source,
    privacy: Privacy,
    moment: datetime | None = None,
) -> SavedMemory:
    """Creates the memory file. Never overwrites; repeated content returns the existing file."""
    sha = memory.sha256
    already = existing(folder, sha)
    if already:
        return SavedMemory(file=already, sha256=sha, created=False)

    moment = (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    name = _file_name(moment, memory.title, sha, source, privacy)
    text = render(memory, ident=name.removesuffix(".md"), author=author, created_at=moment)

    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / name
    temporary = folder / f".writing-{secrets.token_hex(8)}"  # hidden: the scanner does not read it
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temporary, target)  # atomic and never overwrites
        except FileExistsError:
            return SavedMemory(file=name, sha256=sha, created=False)
    finally:
        temporary.unlink(missing_ok=True)
    return SavedMemory(file=name, sha256=sha, created=True)


def forget(folder: Path, name: str, moment: datetime | None = None) -> str:
    """Moves a memory created through MCP to ``.trash/`` (nothing is deleted; to restore, move it back).

    Only accepts names in the format ``save`` generates, and only regular files (symbolic links are refused),
    so the client cannot reach any other file. Returns the file name inside the trash.
    """
    if not GENERATED_NAME.match(name):
        raise MemoryRejected("Only memories created by lore_remember can be forgotten.")
    origin = folder / name
    try:
        info = origin.lstat()
    except FileNotFoundError:
        raise MemoryRejected("That memory no longer exists in the memories folder.") from None
    if not stat.S_ISREG(info.st_mode):
        raise MemoryRejected("Only memories created by lore_remember can be forgotten.")
    moment = (moment or datetime.now(UTC)).astimezone(UTC)
    trash = folder / TRASH
    trash.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_name = f"{moment:%Y%m%d-%H%M%S}-{name}"
    os.link(origin, trash / target_name)  # fails if it already exists: never overwrites
    origin.unlink()
    return target_name


def render(memory: NewMemory, *, ident: str, author: str | None, created_at: datetime) -> str:
    header = {
        "id": ident,
        "title": memory.title,
        "kind": memory.kind,
        "subject": memory.subject,
        "status": "unverified",
        "origin": "mcp lore_remember",
        "author": author or "unidentified MCP client",
        "created_at": created_at.isoformat(),
        "decisions": list(memory.decisions) or None,
        "sha256": memory.sha256,
    }
    frontmatter = yaml.safe_dump(
        {k: v for k, v in header.items() if v is not None}, allow_unicode=True, sort_keys=False, width=1000
    ).strip()
    parts = [
        f"---\n{frontmatter}\n---",
        f"# {memory.title}",
        (
            f"> Memory recorded by an AI agent ({author or 'unidentified client'}) through the lore MCP server "
            f"on {created_at:%Y-%m-%d %H:%M} UTC. **Unverified:** check the sources before treating it as fact."
        ),
        memory.content,
    ]
    if memory.evidence:
        parts.append("## Evidence\n\n" + "\n".join(f"- {_one_line(e)}" for e in memory.evidence))
    if memory.decisions:
        parts.append("## Related decisions\n\n" + "\n".join(f"- {d}" for d in memory.decisions))
    return "\n\n".join(parts) + "\n"


def _file_name(moment: datetime, title: str, sha: str, source: Source, privacy: Privacy) -> str:
    base = f"{moment:%Y-%m-%d-%H%M%S}"
    for summary in (_slug(title), "memory"):
        name = f"{base}-{summary}-{sha[:12]}.md"
        # A title like "database credentials" would give a name the deny list refuses (it would never be read).
        if source.accepts(name) and not privacy.denied(name):
            return name
    raise MemoryRejected("The memories source in sources.yaml does not accept the files written through MCP.")


def _slug(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", ascii_)
    summary = ""
    for word in words:
        candidate = f"{summary}-{word}" if summary else word
        if len(candidate) > 50:
            break
        summary = candidate
    return summary or "memory"


def _clean(value: str | None) -> str:
    return _CONTROL.sub("", value if isinstance(value, str) else "").strip()


def _one_line(text: str) -> str:
    # Evidence never becomes a code block or a heading: it stays on one line.
    return text.replace("\n", " ").lstrip("#").strip()
