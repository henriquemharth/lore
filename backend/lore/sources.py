"""Knowledge sources (sources.yaml), decision code rules and path pattern matching."""

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_VALID_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
_VALID_DOMAIN = re.compile(r"^[a-z][a-z0-9-]{1,20}$")
# Domain of every source that does not declare one.
DEFAULT_DOMAIN = "default"

# Decision codes recognized by default: DEC-001, ADR-001, ADR-0001.
DEFAULT_DECISION_PATTERNS = (r"DEC-\d{3,4}", r"ADR-\d{3,4}")
# Folders whose numbered files are decision records: adr/0001-title.md, docs/decisions/0001-title.md…
DEFAULT_NUMBERED_FOLDERS = ("adr", "adrs", "decisions", "decision-records")
NUMBERED_PREFIX = "ADR"


class ConfigError(ValueError):
    """Invalid sources, taxonomy or privacy file."""


@dataclass(frozen=True)
class DecisionRules:
    """How decision records are recognized: by code in the file name or text, or by numbered file layout.

    ``patterns`` are regular expressions for a whole code (``DEC-\\d{3,4}``). A file whose path contains a
    code is a decision with that code; a code written in the text is a mention of that decision. A file
    ``<folder>/0001-title.md`` inside one of ``numbered_folders`` is the decision ``ADR-0001``.
    """

    patterns: tuple[str, ...] = DEFAULT_DECISION_PATTERNS
    numbered_folders: tuple[str, ...] = DEFAULT_NUMBERED_FOLDERS

    def find_in_text(self, text: str) -> list[str]:
        return [m.group(0) for m in _text_regex(self.patterns).finditer(text)]

    def is_code(self, text: str) -> bool:
        return _full_regex(self.patterns).fullmatch(text) is not None

    def code_from_path(self, path: str) -> str | None:
        found = _path_regex(self.patterns).search(path)
        if found:
            return found.group(1).upper()
        numbered = _numbered_regex(self.numbered_folders).search(path)
        if numbered:
            return f"{NUMBERED_PREFIX}-{numbered.group(1)}"
        return None

    def fingerprint(self) -> str:
        raw = "\n".join([*self.patterns, "--", *self.numbered_folders])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def decision_number(code: str) -> int:
    found = re.search(r"(\d+)\D*$", code)
    return int(found.group(1)) if found else 0


def decision_prefix(code: str) -> str:
    return re.sub(r"[-_ ]?\d+\D*$", "", code).upper()


@lru_cache(maxsize=32)
def _text_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(f"(?:{p})" for p in patterns) + r")\b")


@lru_cache(maxsize=32)
def _full_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


@lru_cache(maxsize=32)
def _path_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"(?:^|/)(" + "|".join(f"(?:{p})" for p in patterns) + r")(?=[-_. ]|$)", re.IGNORECASE
    )


@lru_cache(maxsize=32)
def _numbered_regex(folders: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(f) for f in folders) or "(?!)"
    return re.compile(r"(?:^|/)(?:" + alternatives + r")/(\d{3,4})-[^/]+\.md$", re.IGNORECASE)


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    type: Literal["folder", "git"]
    root: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    ref: str | None = None
    # Folder inside a git repository: files listed in .gitignore stay out, unless the source says otherwise.
    respect_gitignore: bool = True
    # Domain wall: an MCP process only sees the sources of its domain (LORE_MCP_DOMAIN).
    domain: str = DEFAULT_DOMAIN

    def accepts(self, path: str) -> bool:
        """Whether a relative (POSIX) path belongs to this source. Only .md, never hidden."""
        parts = path.split("/")
        if any(p.startswith(".") for p in parts) or not path.lower().endswith(".md"):
            return False
        return any(match(path, p) for p in self.include) and not any(match(path, p) for p in self.exclude)


@dataclass(frozen=True)
class SourcesConfig:
    sources: tuple[Source, ...]
    max_bytes: int
    domain_descriptions: dict[str, str] = field(default_factory=dict)
    decisions: DecisionRules = field(default_factory=DecisionRules)

    def domains(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.domain for s in self.sources))

    def in_domain(self, domain: str) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if s.domain == domain)

    def describe(self, domain: str) -> str | None:
        return self.domain_descriptions.get(domain)


def load_sources(file: Path) -> SourcesConfig:
    """Reads sources.yaml. A relative ``root`` is resolved from the folder that contains the file."""
    file = Path(file)
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Could not read {file}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ConfigError(f"{file}: expected a mapping with a 'sources' list.")

    max_bytes = int(data.get("max_bytes_per_file", 2_000_000))
    if max_bytes <= 0:
        raise ConfigError("max_bytes_per_file must be positive.")

    base = file.resolve().parent
    sources: list[Source] = []
    for item in data["sources"]:
        if not isinstance(item, dict):
            raise ConfigError("Each source must be a mapping.")
        ident = str(item.get("id", ""))
        if not _VALID_ID.match(ident):
            raise ConfigError(f"Invalid source id: {ident!r} (use lowercase letters, digits and '-').")
        if any(s.id == ident for s in sources):
            raise ConfigError(f"Duplicate source id: {ident}")
        kind = item.get("type")
        if kind not in ("folder", "git"):
            raise ConfigError(f"Source {ident}: type must be 'folder' or 'git'.")
        if not item.get("root"):
            raise ConfigError(f"Source {ident}: 'root' is required.")
        ref = item.get("ref")
        if kind == "git":
            if not ref or not _VALID_REF.match(str(ref)) or str(ref).startswith("-"):
                raise ConfigError(f"Source {ident}: 'ref' missing or invalid.")
            ref = str(ref)
        include = tuple(str(p) for p in item.get("include") or ["**/*.md"])
        exclude = tuple(str(p) for p in item.get("exclude") or [])
        respect_gitignore = item.get("respect_gitignore", True)
        if not isinstance(respect_gitignore, bool):
            raise ConfigError(f"Source {ident}: 'respect_gitignore' must be true or false.")
        domain = str(item.get("domain") or DEFAULT_DOMAIN)
        if not _VALID_DOMAIN.match(domain):
            raise ConfigError(f"Source {ident}: invalid domain: {domain!r} (use lowercase letters, digits and '-').")
        root = Path(str(item["root"])).expanduser()
        if not root.is_absolute():
            root = (base / root).resolve()
        sources.append(
            Source(
                id=ident,
                name=str(item.get("name") or ident),
                type=kind,
                root=root,
                include=include,
                exclude=exclude,
                ref=ref if kind == "git" else None,
                respect_gitignore=respect_gitignore,
                domain=domain,
            )
        )
    if not sources:
        raise ConfigError("No sources configured.")
    return SourcesConfig(
        sources=tuple(sources),
        max_bytes=max_bytes,
        domain_descriptions=_domains(data.get("domains")),
        decisions=_decision_rules(data.get("decisions")),
    )


def _domains(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("'domains' must map a domain name to {description: ...}.")
    descriptions: dict[str, str] = {}
    for name, entry in value.items():
        name = str(name)
        if not _VALID_DOMAIN.match(name):
            raise ConfigError(f"domains: invalid domain name {name!r}.")
        if entry is not None and not isinstance(entry, dict):
            raise ConfigError(f"domains.{name}: expected a mapping with 'description'.")
        description = str((entry or {}).get("description") or "").strip()
        if description:
            descriptions[name] = description
    return descriptions


def _decision_rules(value: object) -> DecisionRules:
    if value is None:
        return DecisionRules()
    if not isinstance(value, dict):
        raise ConfigError("'decisions' must be a mapping (patterns, numbered_folders).")
    patterns = tuple(str(p) for p in value.get("patterns") or DEFAULT_DECISION_PATTERNS)
    for pattern in patterns:
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ConfigError(f"decisions.patterns: invalid regular expression {pattern!r}: {error}") from error
        if compiled.fullmatch(""):
            raise ConfigError(f"decisions.patterns: {pattern!r} matches an empty code.")
    folders = value.get("numbered_folders")
    numbered = DEFAULT_NUMBERED_FOLDERS if folders is None else tuple(str(f).strip("/") for f in folders)
    return DecisionRules(patterns=patterns, numbered_folders=numbered)


def match(path: str, pattern: str) -> bool:
    """Matches a relative POSIX path against a glob-style pattern.

    ``*`` and ``?`` do not cross ``/``; ``**`` as a whole segment matches zero or more folders.
    Case-insensitive (like the default macOS file system).
    """
    return _compile(pattern).fullmatch(path) is not None


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    parts = pattern.strip("/").split("/")
    regex = ""
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            regex += ".*" if last else "(?:[^/]+/)*"
            continue
        regex += _segment(part) + ("" if last else "/")
    return re.compile(regex, re.IGNORECASE)


def _segment(part: str) -> str:
    out = []
    i = 0
    while i < len(part):
        c = part[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = part.find("]", i + 1)
            if end == -1:
                out.append(re.escape(c))
            else:
                klass = part[i + 1 : end]
                if klass.startswith("!"):
                    klass = "^" + klass[1:]
                out.append("[" + klass.replace("\\", "\\\\") + "]")
                i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)
