"""Splitting documents into chunks that respect the Markdown sections.

Rules, in order:
1. A whole section (with its subsections) that fits in ``max_chars`` becomes a single chunk.
2. A section that is too large descends into its subsections; its introduction becomes its own chunk,
   or — when short — is glued to the next chunk.
3. Small neighbouring subsections (< ``min_chars``) are grouped up to ``target_chars``.
4. Only a section without subsections that is larger than ``max_chars`` is cut, always between paragraphs;
   a code block is never split unless it alone is over the maximum.

Chunks cover the document body in order, without overlap, and each one keeps its lines and character
positions in the original text (after the frontmatter).
"""

import hashlib
from dataclasses import dataclass, field

from lore.parser import ParsedDocument


@dataclass(frozen=True)
class ChunkingParams:
    max_chars: int = 3000  # characters (~800 tokens)
    target_chars: int = 1800
    min_chars: int = 500


@dataclass
class Chunk:
    ordinal: int
    title: str
    section_path: list[str]
    sections: list[str]
    level: int
    part: int
    parts: int
    line_start: int  # 1-based, inclusive
    line_end: int
    char_start: int
    char_end: int
    text: str
    hash: str


@dataclass
class _Section:
    level: int  # 0 = the document
    title: str
    start: int  # 0-based line of the heading (document: body start)
    end: int
    parent: "_Section | None"
    children: list["_Section"] = field(default_factory=list)

    def path(self) -> list[str]:
        parts: list[str] = []
        current: _Section | None = self
        while current is not None and current.level > 0:
            parts.append(current.title)
            current = current.parent
        return list(reversed(parts))


@dataclass
class _Seg:
    start: int
    end: int
    section: _Section
    sections: list[_Section]
    part: int = 1
    parts: int = 1


def split(doc: ParsedDocument, params: ChunkingParams | None = None) -> list[Chunk]:
    return _Splitter(doc, params or ChunkingParams()).run()


class _Splitter:
    def __init__(self, doc: ParsedDocument, p: ChunkingParams) -> None:
        self.doc = doc
        self.p = p
        self.lines = doc.lines
        self.offsets: list[int] = []
        position = 0
        for line in self.lines:
            self.offsets.append(position)
            position += len(line) + 1
        self.block_at = {a: b for a, b in doc.code_blocks}

    def size(self, a: int, b: int) -> int:
        if a > b:
            return 0
        return self.offsets[b] + len(self.lines[b]) - self.offsets[a]

    def run(self) -> list[Chunk]:
        root = self._tree()
        if root.start > root.end:
            return []
        return self._finish(self._segment(root, root.start, root.end))

    def _tree(self) -> _Section:
        last = len(self.lines) - 1
        root = _Section(0, self.doc.title, self.doc.body_start, last, None)
        stack = [root]
        for h in sorted(self.doc.headings, key=lambda x: x.line):
            line = h.line - 1
            while stack[-1].level >= h.level:
                stack.pop().end = line - 1
            section = _Section(h.level, h.text, line, last, stack[-1])
            stack[-1].children.append(section)
            stack.append(section)
        return root

    def _segment(self, section: _Section, start: int, end: int) -> list[_Seg]:
        """Returns segments that cover exactly the lines [start, end] of the section."""
        if start > end:
            return []
        if self.size(start, end) <= self.p.max_chars:
            return [_Seg(start, end, section, [section])]
        if not section.children:
            return self._split(section, start, end)

        segs: list[_Seg] = []
        carry: int | None = None  # short introduction glued to the next chunk
        first = section.children[0].start
        if start <= first - 1:
            if self.size(start, first - 1) < self.p.min_chars:
                carry = start
            else:
                segs.extend(self._split(section, start, first - 1))

        group: list[_Section] = []

        def close_group() -> None:
            nonlocal carry
            if not group:
                return
            begin = carry if carry is not None else group[0].start
            carry = None
            owner = group[0] if len(group) == 1 else section
            segs.append(_Seg(begin, group[-1].end, owner, list(group)))
            group.clear()

        for child in section.children:
            if self.size(child.start, child.end) < self.p.min_chars:
                if group and self.size(group[0].start, child.end) > self.p.target_chars:
                    close_group()
                group.append(child)
                continue
            close_group()
            begin = carry if carry is not None else child.start
            carry = None
            segs.extend(self._segment(child, begin, child.end))
        close_group()
        return segs

    def _split(self, section: _Section, start: int, end: int) -> list[_Seg]:
        if self.size(start, end) <= self.p.max_chars:
            return [_Seg(start, end, section, [section])]
        groups: list[tuple[int, int]] = []
        current: tuple[int, int] | None = None
        for a, b in self._blocks(start, end):
            if self.size(a, b) > self.p.max_chars:
                begin = a
                if current:
                    if self.size(*current) < self.p.min_chars:
                        begin = current[0]  # short paragraph before the large block goes with it
                    else:
                        groups.append(current)
                    current = None
                groups.extend(self._by_lines(begin, b))
            elif current is None:
                current = (a, b)
            elif self.size(current[0], b) <= self.p.target_chars or (
                self.size(*current) < self.p.min_chars and self.size(current[0], b) <= self.p.max_chars
            ):
                current = (current[0], b)
            else:
                groups.append(current)
                current = (a, b)
        if current:
            groups.append(current)
        # A very short last piece goes back to the previous one, if it fits.
        if len(groups) >= 2:
            (a1, _), (a2, b2) = groups[-2], groups[-1]
            if self.size(a2, b2) < self.p.min_chars and self.size(a1, b2) <= self.p.max_chars:
                groups[-2:] = [(a1, b2)]
        return [_Seg(a, b, section, [section], part=i + 1, parts=len(groups)) for i, (a, b) in enumerate(groups)]

    def _blocks(self, start: int, end: int) -> list[tuple[int, int]]:
        """Paragraphs (separated by a blank line); a code block counts as one."""
        blocks: list[tuple[int, int]] = []
        i = start
        while i <= end:
            begin = i
            while i <= end and not self.lines[i].strip():
                i += 1
            if i > end:
                if blocks:
                    blocks[-1] = (blocks[-1][0], end)
                else:
                    blocks.append((begin, end))
                break
            while i <= end:
                if i in self.block_at:
                    i = min(self.block_at[i], end) + 1
                    continue
                if not self.lines[i].strip():
                    break
                i += 1
            blocks.append((begin, i - 1))
        return blocks

    def _by_lines(self, a: int, b: int) -> list[tuple[int, int]]:
        groups: list[tuple[int, int]] = []
        begin = a
        for j in range(a, b + 1):
            if j > begin and self.size(begin, j) > self.p.target_chars:
                groups.append((begin, j - 1))
                begin = j
        groups.append((begin, b))
        return groups

    def _finish(self, segs: list[_Seg]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for seg in segs:
            a, b = seg.start, seg.end
            while a <= b and not self.lines[a].strip():
                a += 1
            while b >= a and not self.lines[b].strip():
                b -= 1
            if a > b:
                continue
            start = self.offsets[a]
            end = self.offsets[b] + len(self.lines[b])
            text = self.doc.text[start:end]
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    title=seg.section.title,
                    section_path=seg.section.path(),
                    sections=[s.title for s in seg.sections],
                    level=seg.section.level,
                    part=seg.part,
                    parts=seg.parts,
                    line_start=a + 1,
                    line_end=b + 1,
                    char_start=start,
                    char_end=end,
                    text=text,
                    hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
        return chunks
