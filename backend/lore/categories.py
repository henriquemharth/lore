"""Classification of documents in the knowledge tree (taxonomy.yaml).

Every assigned category carries its origin: ``explicit`` when the source itself says so (frontmatter, source,
folder, file name — according to the rule declared in the taxonomy) and ``suggestion`` when it is an
interpretation (a rule marked as suggestion, or a keyword). Nothing is assigned without a reason.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from lore.sources import ConfigError, match

UNCATEGORIZED = "Uncategorized"
CONFIDENCES = ("explicit", "suggestion")
# Frontmatter keys that name a category (English, plus the Portuguese forms).
_FRONTMATTER_KEYS = ("category", "categories", "tags", "categoria", "categorias")


@dataclass(frozen=True)
class Assignment:
    category: str
    primary: bool
    confidence: str  # explicit | suggestion
    origin: str  # frontmatter | rule | keyword | none
    reason: str  # text shown in the UI
    line: int | None = None  # line of the document that supports the assignment, when there is one

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Rule:
    category: str
    sources: tuple[str, ...]
    paths: tuple[str, ...]
    confidence: str
    reason: str

    def matches(self, source_id: str, path: str) -> bool:
        if self.sources and source_id not in self.sources:
            return False
        return not self.paths or any(match(path, p) for p in self.paths)


@dataclass(frozen=True)
class Tag:
    category: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Taxonomy:
    root: str
    categories: tuple[str, ...]
    rules: tuple[Rule, ...]
    tags: tuple[Tag, ...]

    def classify(
        self,
        source_id: str,
        path: str,
        title: str,
        headings: list[tuple[str, int]],
        frontmatter: dict[str, Any] | None = None,
    ) -> list[Assignment]:
        """Assignments of the document; the first one is the primary.

        ``headings`` are (text, line) pairs. Precedence of the primary category:
        frontmatter → first matching rule → best keyword (suggestion) → "Uncategorized".
        """
        primary = self._from_frontmatter(frontmatter)
        if primary is None:
            rule = next((r for r in self.rules if r.matches(source_id, path)), None)
            if rule is not None:
                primary = Assignment(rule.category, True, rule.confidence, "rule", rule.reason)

        suggestions = self._from_keywords(path, title, headings)
        if primary is None:
            if suggestions:
                first = suggestions[0]
                primary = Assignment(
                    first.category, True, "suggestion", "keyword", f"no explicit rule; {first.reason}", first.line
                )
            else:
                primary = Assignment(
                    UNCATEGORIZED, True, "suggestion", "none", "no rule nor keyword identified the subject"
                )
        extras = [s for s in suggestions if s.category != primary.category]
        return [primary, *self._sort(extras)]

    def _from_frontmatter(self, frontmatter: dict[str, Any] | None) -> Assignment | None:
        if not frontmatter:
            return None
        by_name = {normalize(c): c for c in self.categories}
        by_leaf: dict[str, list[str]] = {}
        for c in self.categories:
            by_leaf.setdefault(normalize(c.rsplit("/", 1)[-1]), []).append(c)
        for key in _FRONTMATTER_KEYS:
            value = frontmatter.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    continue
                target = normalize(item.strip().strip("/"))
                found = by_name.get(target)
                if found is None and len(by_leaf.get(target, [])) == 1:
                    found = by_leaf[target][0]
                if found:
                    return Assignment(found, True, "explicit", "frontmatter", f'frontmatter "{key}: {item.strip()}"')
        return None

    def _from_keywords(self, path: str, title: str, headings: list[tuple[str, int]]) -> list[Assignment]:
        places: list[tuple[str, str, int | None]] = [
            ("file name", Path(path).stem.replace("-", " ").replace("_", " "), None),
            ("title", title, None),
            *[(f"heading “{text}”", text, line) for text, line in headings],
        ]
        out: list[Assignment] = []
        for tag in self.tags:
            for where, text, line in places:
                found = tag.pattern.search(normalize(text))
                if found:
                    out.append(
                        Assignment(tag.category, False, "suggestion", "keyword", f"word “{found.group(0)}” in the {where}", line)
                    )
                    break
        return self._sort(out)

    def _sort(self, assignments: list[Assignment]) -> list[Assignment]:
        order = {c: i for i, c in enumerate(self.categories)}
        seen: dict[str, Assignment] = {}
        for a in assignments:
            seen.setdefault(a.category, a)
        return sorted(seen.values(), key=lambda a: order.get(a.category, len(order)))


def normalize(text: str) -> str:
    unaccented = unicodedata.normalize("NFKD", text)
    return "".join(c for c in unaccented if not unicodedata.combining(c)).lower()


def load_taxonomy(file: Path) -> Taxonomy:
    try:
        data = yaml.safe_load(Path(file).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Could not read {file}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{file}: expected a mapping.")

    declared = [str(c).strip("/") for c in data.get("categories") or []]
    if len(set(declared)) != len(declared):
        raise ConfigError("taxonomy: duplicate category.")
    # Upper levels exist even when not listed ("Engineering/Database" creates "Engineering").
    categories: list[str] = []
    for c in declared:
        parts = c.split("/")
        for i in range(1, len(parts) + 1):
            level = "/".join(parts[:i])
            if level not in categories:
                categories.append(level)
    known = set(categories)

    def check(category: object) -> str:
        name = str(category or "").strip("/")
        if name not in known:
            raise ConfigError(f"taxonomy: category {name!r} is not in the 'categories' list.")
        return name

    rules = []
    for index, r in enumerate(data.get("rules") or [], start=1):
        confidence = str(r.get("confidence") or "")
        reason = str(r.get("reason") or "").strip()
        if confidence not in CONFIDENCES:
            raise ConfigError(f"taxonomy: rule {index} needs confidence 'explicit' or 'suggestion'.")
        if not reason:
            raise ConfigError(f"taxonomy: rule {index} needs a reason.")
        rules.append(
            Rule(
                category=check(r.get("category")),
                sources=tuple(str(s) for s in r.get("sources") or []),
                paths=tuple(str(p) for p in r.get("paths") or []),
                confidence=confidence,
                reason=reason,
            )
        )
    tags = []
    for t in data.get("tags") or []:
        words = [normalize(str(w)) for w in t.get("words") or [] if str(w).strip()]
        if not words:
            continue
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\w*")
        tags.append(Tag(category=check(t.get("category")), pattern=pattern))

    return Taxonomy(
        root=str(data.get("root") or "Lore"),
        categories=tuple(categories),
        rules=tuple(rules),
        tags=tuple(tags),
    )
