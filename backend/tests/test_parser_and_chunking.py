"""Parser (optional frontmatter, headings, links, metadata) and splitting into chunks."""

import textwrap
import unittest

from lore.chunking import ChunkingParams, split
from lore.parser import parse


def md(text: str) -> bytes:
    return textwrap.dedent(text).lstrip("\n").encode("utf-8")


class TestParser(unittest.TestCase):
    def test_works_without_frontmatter(self):
        doc = parse(md("# Architecture\n\nText.\n\n## Stack\nPython.\n"), "02-ARCHITECTURE.md")
        self.assertIsNone(doc.frontmatter)
        self.assertEqual("Architecture", doc.title)
        self.assertEqual(0, doc.body_start)
        self.assertEqual([(1, "Architecture", 1), (2, "Stack", 5)], [(h.level, h.text, h.line) for h in doc.headings])
        self.assertEqual([], doc.warnings)

    def test_frontmatter_is_read_and_stays_out_of_the_body(self):
        doc = parse(
            md("""
            ---
            name: acme-shop-stack
            description: Real stack of the shop
            date: 2026-09-15
            ---
            Body without a title.
            """),
            "memory.md",
        )
        self.assertEqual(
            {"name": "acme-shop-stack", "description": "Real stack of the shop", "date": "2026-09-15"}, doc.frontmatter
        )
        self.assertEqual("acme-shop-stack", doc.title)  # no H1: uses name
        self.assertEqual(5, doc.body_start)
        self.assertEqual("Real stack of the shop", doc.description)

    def test_invalid_frontmatter_becomes_a_warning_and_plain_text(self):
        doc = parse(md("---\nkey: [unclosed\n---\n# Title\n"), "x.md")
        self.assertIsNone(doc.frontmatter)
        self.assertEqual(0, doc.body_start)
        self.assertTrue(any("Invalid frontmatter" in w for w in doc.warnings))
        self.assertEqual("Title", doc.title)

    def test_title_falls_back_to_the_file_name(self):
        self.assertEqual("NO-TITLE", parse(b"just text\n", "folder/NO-TITLE.md").title)

    def test_heading_inside_a_code_block_does_not_count(self):
        doc = parse(md("# A\n\n```bash\n# comment\n```\n\nSetext\n------\n"), "x.md")
        self.assertEqual(["A", "Setext"], [h.text for h in doc.headings])
        self.assertEqual([(2, 4)], doc.code_blocks)

    def test_heading_without_markdown_markup(self):
        doc = parse(md("## Queue of **orders** and `audit_log`\n"), "x.md")
        self.assertEqual("Queue of orders and audit_log", doc.headings[0].text)

    def test_links_wikilink_alias_anchor_embed_and_markdown(self):
        doc = parse(
            md("""
            See [[01-CURRENT-STATE]], [[decisions/DEC-025|the matrix]] and [[06-SECURITY#Sessions]].
            Image ![[diagram]] and [relative](../bugs/PITFALLS.md#top) and [site](https://example.com/a.md).
            In code `[[not-a-link]]` and:

            ```
            [[not-either]]
            ```
            """),
            "modules/HR.md",
        )
        links = [(lk.type, lk.target, lk.anchor, lk.text) for lk in doc.links]
        self.assertEqual(
            [
                ("wikilink", "01-CURRENT-STATE", None, None),
                ("wikilink", "decisions/DEC-025", None, "the matrix"),
                ("wikilink", "06-SECURITY", "Sessions", None),
                ("embed", "diagram", None, None),
                ("markdown", "../bugs/PITFALLS.md", "top", "relative"),
            ],
            links,
        )
        self.assertEqual([1, 1, 1, 2, 2], [lk.line for lk in doc.links])

    def test_list_metadata_at_the_top_of_decision_records(self):
        doc = parse(
            md("""
            # DEC-042 — Security center

            - **Date:** 2026-09-23
            - **Status:** Accepted and **published on 2026-09-23**, commit
              **`60041bb`** (just one).
            - **Full contract:** `docs/SECURITY-CENTER.md`.

            ## Context
            - **This:** is not metadata (past the top).
            """),
            "decisions/DEC-042.md",
        )
        self.assertEqual("2026-09-23", doc.metadata["Date"])
        self.assertIn("60041bb", doc.metadata["Status"])
        self.assertIn("Full contract", doc.metadata)
        self.assertNotIn("This", doc.metadata)

    def test_plain_list_metadata_as_in_madr_templates(self):
        doc = parse(
            md("""
            # ADR-0004 — Move orders to PostgreSQL 16

            - Status: Superseded by ADR-0007
            * Date: 2025-11-02
            - see the runbook: not metadata (lower-case key)

            ## Context
            - Owner: not metadata (past the top)
            """),
            "docs/adr/0004-move-orders.md",
        )
        self.assertEqual({"Status": "Superseded by ADR-0007", "Date": "2025-11-02"}, doc.metadata)

    def test_crlf_bom_and_invalid_utf8(self):
        doc = parse(b"\xef\xbb\xbf# T\xc3\xadtle\r\n\r\nLine \xff broken\r\n", "x.md")
        self.assertEqual("Títle", doc.title)
        self.assertNotIn("\r", doc.text)
        self.assertIn("�", doc.text)
        self.assertTrue(doc.warnings)


class TestChunking(unittest.TestCase):
    P = ChunkingParams(max_chars=400, target_chars=250, min_chars=80)

    def _coverage(self, doc, chunks):
        """Every non-empty body line is in exactly one chunk, in order."""
        count = [0] * len(doc.lines)
        for c in chunks:
            for i in range(c.line_start - 1, c.line_end):
                count[i] += 1
        for i in range(doc.body_start, len(doc.lines)):
            if doc.lines[i].strip():
                self.assertEqual(1, count[i], f"line {i + 1}: {doc.lines[i]!r}")
        starts = [c.line_start for c in chunks]
        self.assertEqual(sorted(starts), starts)

    def test_small_document_becomes_one_chunk(self):
        doc = parse(md("# Small\n\nA paragraph.\n\n## Section\nAnother.\n"), "p.md")
        chunks = split(doc, self.P)
        self.assertEqual(1, len(chunks))
        c = chunks[0]
        self.assertEqual((1, 6), (c.line_start, c.line_end))
        self.assertEqual(doc.text[c.char_start : c.char_end], c.text)
        self.assertEqual(64, len(c.hash))

    def test_large_document_splits_by_section_and_keeps_the_path(self):
        body = "\n\n".join(
            f"## Section {n}\n\n" + "\n\n".join(f"Paragraph {n}.{p} " + "text " * 12 for p in range(3))
            for n in range(1, 4)
        )
        doc = parse(md("# Large\n\nShort intro.\n\n") + body.encode(), "g.md")
        chunks = split(doc, self.P)
        self._coverage(doc, chunks)
        self.assertGreaterEqual(len(chunks), 3)
        for c in chunks:
            self.assertEqual("Large", c.section_path[0])
            self.assertTrue(c.section_path[-1].startswith("Section"))
            self.assertEqual(doc.text[c.char_start : c.char_end], c.text)
        # the short introduction is glued to the first chunk, not left alone
        self.assertEqual(1, chunks[0].line_start)
        self.assertIn("Short intro", chunks[0].text)

    def test_large_section_without_subsections_is_cut_between_paragraphs(self):
        paragraphs = [f"Paragraph {i} " + "word " * 20 for i in range(6)]
        doc = parse(md("## Single\n\n" + "\n\n".join(paragraphs) + "\n"), "u.md")
        chunks = split(doc, self.P)
        self._coverage(doc, chunks)
        self.assertGreater(len(chunks), 1)
        self.assertEqual([len(chunks)] * len(chunks), [c.parts for c in chunks])
        self.assertEqual(list(range(1, len(chunks) + 1)), [c.part for c in chunks])
        for c in chunks:
            self.assertTrue(c.text.startswith(("## Single", "Paragraph")), c.text[:30])
            self.assertTrue(c.text.rstrip().endswith("word"), "cut in the middle of a paragraph")

    def test_code_block_is_not_split(self):
        code = "```python\n" + "\n\n".join(f"line_{i} = {i}" for i in range(12)) + "\n```"
        text = "## Code\n\nBefore " + "x " * 60 + "\n\n" + code + "\n\nAfter " + "y " * 60 + "\n"
        doc = parse(text.encode(), "c.md")
        chunks = split(doc, self.P)
        self._coverage(doc, chunks)
        owner = [c for c in chunks if "```python" in c.text]
        self.assertEqual(1, len(owner))
        self.assertEqual(owner[0].text.count("```"), 2)

    def test_small_subsections_are_grouped(self):
        subs = "\n\n".join(f"### Item {i}\nShort {i}." for i in range(30))
        text = "## List\n\n" + subs + "\n\n## Other\n\n" + "long " * 80 + "\n"
        doc = parse(text.encode(), "l.md")
        chunks = split(doc, self.P)
        self._coverage(doc, chunks)
        grouped = [c for c in chunks if len(c.sections) > 1]
        self.assertTrue(grouped)
        self.assertTrue(all(s.startswith("Item") for c in grouped for s in c.sections))

    def test_frontmatter_stays_out_of_chunks_and_empty_gives_no_chunk(self):
        doc = parse(md("---\nname: x\n---\nBody.\n"), "m.md")
        chunks = split(doc, self.P)
        self.assertEqual(1, len(chunks))
        self.assertEqual((4, 4), (chunks[0].line_start, chunks[0].line_end))
        self.assertEqual([], split(parse(b"---\nname: x\n---\n", "v.md"), self.P))
        self.assertEqual([], split(parse(b"", "empty.md"), self.P))

    def test_default_params_respect_the_maximum_on_real_text(self):
        sections = "\n\n".join(
            f"## Delivery {i} — 2026-09-2{i}\n\n" + ("Relevant fact. " * 40 + "\n\n") * 3 for i in range(10)
        )
        doc = parse(("# Current state\n\n" + sections).encode(), "01-CURRENT-STATE.md")
        chunks = split(doc)
        self._coverage(doc, chunks)
        self.assertTrue(all(len(c.text) <= ChunkingParams().max_chars for c in chunks))
        self.assertTrue(all(c.section_path[-1].startswith("Delivery") for c in chunks))


if __name__ == "__main__":
    unittest.main()
