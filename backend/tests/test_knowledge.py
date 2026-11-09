"""Categories with origin, explicit mentions, traceable graph, decisions and privacy."""

import textwrap
import unittest
from contextlib import closing

from support import TAXONOMY, Env, create_repository, write

from lore import graph as graph_module
from lore.categories import load_taxonomy
from lore.db import connect
from lore.decisions import list_decisions, relevant
from lore.entities import extract
from lore.indexer import RunContext
from lore.parser import parse
from lore.privacy import Privacy, load_privacy, redact
from lore.scanner import scan_folder
from lore.sources import ConfigError, Source

CONTEXT = RunContext(origin="test")


def md(text: str) -> bytes:
    return textwrap.dedent(text).lstrip("\n").encode("utf-8")


class TestCategories(unittest.TestCase):
    def setUp(self):
        self.tax = load_taxonomy(TAXONOMY)

    def classify(self, source, path, title="", headings=(), frontmatter=None):
        return self.tax.classify(source, path, title, list(headings), frontmatter)

    def test_root_and_tree(self):
        self.assertEqual("Lore", self.tax.root)
        for c in ("Product", "Engineering", "Engineering/Architecture", "Engineering/Backend", "Engineering/Frontend",
                  "Engineering/Security", "Engineering/Infrastructure", "Engineering/Deployment", "Processes",
                  "Decisions", "Memories"):
            self.assertIn(c, self.tax.categories)

    def test_author_folder_is_explicit_with_a_reason(self):
        primary = self.classify("notes", "decisions/DEC-001-X.md", "DEC-001")[0]
        self.assertEqual(("Decisions", "explicit", "rule"), (primary.category, primary.confidence, primary.origin))
        self.assertIn("decisions/", primary.reason)

    def test_interpretation_is_a_suggestion(self):
        primary = self.classify("notes", "integrations/SERVER-SETUP.md", "Server setup")[0]
        self.assertEqual(("Engineering/Infrastructure", "suggestion"), (primary.category, primary.confidence))

    def test_keyword_only_suggests_and_says_where_it_was_found(self):
        assignments = self.classify("notes", "modules/HR.md", "HR", [("Permissions per tab", 12)])
        extra = next(a for a in assignments if a.category == "Engineering/Security")
        self.assertEqual(("suggestion", "keyword", 12), (extra.confidence, extra.origin, extra.line))
        self.assertIn("Permissions per tab", extra.reason)

    def test_without_a_rule_the_best_suggestion_becomes_primary_marked_as_suggestion(self):
        primary = self.classify("notes", "loose/NOTE.md", "Server backup")[0]
        self.assertEqual(
            ("Engineering/Infrastructure", "suggestion", "keyword"), (primary.category, primary.confidence, primary.origin)
        )
        self.assertIn("no explicit rule", primary.reason)
        nothing = self.classify("notes", "loose/X.md", "Cake recipe")[0]
        self.assertEqual(("Uncategorized", "suggestion"), (nothing.category, nothing.confidence))

    def test_explicit_frontmatter_beats_rules(self):
        primary = self.classify("notes", "decisions/DEC-9.md", "x", frontmatter={"category": "security"})[0]
        self.assertEqual(
            ("Engineering/Security", "explicit", "frontmatter"), (primary.category, primary.confidence, primary.origin)
        )

    def test_rule_without_confidence_or_reason_is_refused(self):
        env = Env()
        try:
            for rule in (
                "{category: Engineering, confidence: explicit}",
                "{category: Engineering, reason: x}",
                "{category: Engineering, confidence: maybe, reason: x}",
                "{category: Missing, confidence: explicit, reason: x}",
            ):
                file = env.base / "t.yaml"
                file.write_text(f"categories: [Engineering]\nrules:\n  - {rule}\n", encoding="utf-8")
                with self.subTest(rule=rule), self.assertRaises(ConfigError):
                    load_taxonomy(file)
        finally:
            env.close()


class TestMentions(unittest.TestCase):
    def mentions(self, text, path="x.md"):
        return {(m.type, m.key) for m in extract(parse(md(text), path))}

    def test_written_decision_commit_migration_and_file(self):
        found = self.mentions("""
            See DEC-031, DEC-042 and ADR-0007.
            Published in commit `60041bb` (full hash `60041bbf9fd5a1b2c3d4e5f60718293a4b5c6d7e`).
            Migration `b7d9f1a3c5e8` applied.
            Touches `backend/app/permissions.py:504` and `frontend/src/navigation.ts`.
        """)
        self.assertEqual(
            {("decision", "DEC-031"), ("decision", "DEC-042"), ("decision", "ADR-0007"), ("commit", "60041bb"),
             ("migration", "b7d9f1a3c5e8"), ("file", "backend/app/permissions.py"), ("file", "frontend/src/navigation.ts")},
            found,
        )

    def test_does_not_invent(self):
        found = self.mentions("""
            Text about decisions and commits without any code; word deadbeef without backticks; `facade`.
            Loose hash 60041bb outside backticks. `a1b2c3d4e5f6` without context. ADR-1 is too short.
            Spreadsheet `People/Salaries.xlsx`, `~/secret.json`, `/opt/acme/.env`, `https://x.com/a.py`.

            ```
            DEC-099 and `b7d9f1a3c5e8` inside a code block
            ```
        """)
        self.assertEqual({("hash", "a1b2c3d4e5f6")}, found)

    def test_keeps_line_and_excerpt(self):
        m = extract(parse(md("# T\n\nline with DEC-007\n"), "x.md"))[0]
        self.assertEqual((3, "line with DEC-007"), (m.line, m.excerpt))
        self.assertIn("DEC-007", m.rule)


class NotesBase(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        n = self.env.notes
        write(n, "00-INDEX.md", "# Index\n\n- [[decisions/DEC-001-Scope]]\n- [[06-SECURITY]]\n")
        write(n, "06-SECURITY.md", "# Security\n\n## Sessions\nThe session ends after inactivity. See `docs/CENTER.md`.\n")
        write(n, "decisions/DEC-001-Scope.md", "# DEC-001 — Scope\n\n- **Date:** 2026-08-01\n- **Status:** Accepted\n\nComplement the shop.\n")
        write(n, "decisions/DEC-002-Old.md", "# DEC-002 — Old rule\n\n- **Status:** Accepted\n\nOld session rule.\n")
        write(n, "decisions/DEC-003-New.md", "# DEC-003 — New rule\n\n- **Status:** Accepted\n\nThis decision supersedes DEC-002 for the session rule.\nIt also cites DEC-001.\n")
        write(n, "decisions/DEC-004-Withdrawn.md", "# DEC-004 — Idea\n\n- **Status:** **Withdrawn before release**\n\nLong session.\n")
        write(n, "decisions/DEC-005-NoStatus.md", "# DEC-005 — No status\n\nSession text without a status.\n")
        write(n, "docs/CENTER.md", "# Center\n\nCommit `60041bb` published. Cites DEC-099, which does not exist.\n")
        self.env.indexer().run(CONTEXT)

    def tearDown(self):
        self.env.close()

    def conn(self):
        return closing(connect(self.env.db))


class TestGraph(NotesBase):
    def test_every_edge_has_evidence_and_a_known_type(self):
        with self.conn() as conn:
            g = graph_module.build(conn, load_taxonomy(TAXONOMY))
        self.assertTrue(g.edges)
        for e in g.edges.values():
            self.assertIn(e["type"], graph_module.EDGE_TYPES)
            self.assertTrue(e["evidence"], e["id"])
            for ev in e["evidence"]:
                self.assertTrue(ev["rule"], e["id"])
            self.assertNotEqual(e["from"], e["to"])
            if e["type"] in ("link", "mentions"):
                self.assertTrue(all(ev["line"] and ev["excerpt"] for ev in e["evidence"]), e["id"])

    def test_written_relations_become_edges(self):
        with self.conn() as conn:
            g = graph_module.build(conn, load_taxonomy(TAXONOMY))
            ids = {d["path"]: f"doc:{d['id']}" for d in conn.execute("SELECT id, path FROM document")}
        edges = {(e["type"], e["from"], e["to"]) for e in g.edges.values()}
        self.assertIn(("link", ids["00-INDEX.md"], ids["decisions/DEC-001-Scope.md"]), edges)
        self.assertIn(("mentions", ids["decisions/DEC-003-New.md"], ids["decisions/DEC-002-Old.md"]), edges)
        self.assertIn(("mentions", ids["06-SECURITY.md"], ids["docs/CENTER.md"]), edges)  # .md path in code
        self.assertIn(("mentions", ids["docs/CENTER.md"], "ent:commit:60041bb"), edges)
        self.assertIn(("mentions", ids["docs/CENTER.md"], "ent:decision:DEC-099"), edges)
        self.assertEqual("decision", g.nodes[ids["decisions/DEC-001-Scope.md"]]["type"])
        self.assertEqual("decision", g.nodes["ent:decision:DEC-099"]["type"])
        # category with confidence and reason
        cat = g.edges[f"category|{ids['decisions/DEC-001-Scope.md']}|cat:Decisions"]
        self.assertEqual("explicit", cat["confidence"])
        self.assertIn("decisions/", cat["evidence"][0]["rule"])

    def test_no_relation_without_text(self):
        with self.conn() as conn:
            g = graph_module.build(conn, load_taxonomy(TAXONOMY))
            ids = {d["path"]: f"doc:{d['id']}" for d in conn.execute("SELECT id, path FROM document")}
        # DEC-004 and DEC-005 talk about sessions, but nothing written links them to 06-SECURITY
        pairs = {(e["from"], e["to"]) for e in g.edges.values() if e["type"] in ("link", "mentions")}
        for dec in ("decisions/DEC-004-Withdrawn.md", "decisions/DEC-005-NoStatus.md"):
            self.assertNotIn((ids[dec], ids["06-SECURITY.md"]), pairs)
            self.assertNotIn((ids["06-SECURITY.md"], ids[dec]), pairs)

    def test_find_and_detail(self):
        with self.conn() as conn:
            g = graph_module.build(conn, load_taxonomy(TAXONOMY))
            self.assertEqual("ent:commit:60041bb", graph_module.find(g, "60041bb")[0]["node"]["id"])
            dec = graph_module.find(g, "DEC-002")[0]["node"]
            self.assertEqual("decision", dec["type"])
            self.assertEqual([], graph_module.find(g, "Authentication"))
            detail = graph_module.detail(conn, g, dec["id"])
        self.assertTrue(detail["own_chunks"])
        self.assertTrue(detail["related_chunks"])  # the chunk of DEC-003 that cites it
        self.assertEqual({"document_id": dec["document_id"], "line": None}, detail["open"])


class TestDecisions(NotesBase):
    def test_state_comes_from_what_is_written(self):
        with self.conn() as conn:
            by_code = {d["code"]: d for d in list_decisions(conn)}
        self.assertEqual("active", by_code["DEC-001"]["state"])
        self.assertEqual("2026-08-01", by_code["DEC-001"]["date"])
        self.assertEqual("Scope", by_code["DEC-001"]["title"])
        self.assertEqual("deprecated", by_code["DEC-004"]["state"])
        self.assertEqual("undeclared", by_code["DEC-005"]["state"])
        old = by_code["DEC-002"]
        self.assertEqual("superseded", old["state"])  # DEC-003 writes "supersedes DEC-002"
        self.assertEqual("DEC-003", old["superseded_by"][0]["code"])
        self.assertIn("supersedes DEC-002", old["superseded_by"][0]["excerpt"])

    def test_a_later_citation_is_not_a_supersede(self):
        with self.conn() as conn:
            dec1 = next(d for d in list_decisions(conn) if d["code"] == "DEC-001")
        self.assertEqual("active", dec1["state"])
        self.assertEqual(["DEC-003"], [c["code"] for c in dec1["cited_by_later_decisions"]])

    def test_relevant_by_subject(self):
        with self.conn() as conn:
            codes = [d["code"] for d in relevant(conn, "session rule")["decisions"]]
            self.assertIn("DEC-002", codes)
            self.assertIn("DEC-003", codes)
            self.assertEqual([], relevant(conn, "xyzzy qwerty")["decisions"])


class TestPrivacy(unittest.TestCase):
    def test_deny_by_name_without_catching_a_decision_about_passwords(self):
        p = Privacy()
        for denied in (".env", "config/.env.local", "key.pem", "id_rsa", "notes/secrets.md", "CREDENTIALS.md", "aws-credential.md"):
            self.assertTrue(p.denied(denied), denied)
        for free in ("decisions/DEC-008-Password-Login.md", "06-SECURITY.md", "design-tokens.md"):
            self.assertFalse(p.denied(free), free)

    def test_sensitive_by_rule_of_the_file(self):
        env = Env()
        try:
            file = env.base / "privacy.yaml"
            file.write_text("sensitive:\n  - sources: [notes]\n    paths: ['hr/*.md']\n    reason: personal data\n", encoding="utf-8")
            p = load_privacy(file)
            self.assertEqual("personal data", p.sensitive_reason("notes", "hr/payroll.md"))
            self.assertIsNone(p.sensitive_reason("other", "hr/payroll.md"))
            self.assertTrue(p.denied(".env"))  # the default list still applies
            file.write_text("sensitive:\n  - sources: [notes]\n    reason: no paths\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_privacy(file)
        finally:
            env.close()

    def test_secret_redaction(self):
        text = "\n".join([
            "SHOP_JWT_SECRET=abcdefghijklmnopqrstuvwxyz123456",
            "MYSQL_PASSWORD=${MYSQL_PASSWORD}",
            "SHOP_ADMIN_PASSWORD=<define>",
            "token: ghp_" + "a" * 36,
            "key sk-ant-" + "b" * 30,
            "-----BEGIN PRIVATE KEY-----\nMIIEv\n-----END PRIVATE KEY-----",
            "mysql+pymysql://shop:superSecret@mysql:3306/shop",
            "The JWT_SECRET needs 32 characters (no value here).",
            "TOKEN_API=tok123456789 and SECRET_KEY=keySecret77",
            "APP_SENHA=senhaForte99",
        ])
        redacted, n = redact(text)
        self.assertEqual(8, n)
        for leaked in ("abcdefghijklmnop", "ghp_aaaa", "sk-ant-bbb", "MIIEv", "superSecret", "tok123456789", "keySecret77", "senhaForte99"):
            self.assertNotIn(leaked, redacted)
        for kept in ("${MYSQL_PASSWORD}", "<define>", "JWT_SECRET needs 32 characters", "SHOP_JWT_SECRET="):
            self.assertIn(kept, redacted)
        self.assertIn("[REDACTED: secret value]", redacted)

    def test_scanner_does_not_read_denied_or_gitignored(self):
        env = Env()
        try:
            repo = env.base / "agent-notes"
            create_repository(repo, {".gitignore": "draft-*.md\n", "MEMORY.md": "# M\n"})
            write(repo, "draft-1.md", "# ignored by git\n")
            write(repo, "secrets.md", "# denied\n")
            source = Source("mem", "Mem", "folder", repo, ("*.md",))
            s = scan_folder(source, 100_000, Privacy())
            self.assertEqual(["MEMORY.md"], [f.path for f in s.files])
            self.assertTrue(any("gitignore" in w for w in s.warnings))
            self.assertTrue(any("deny list" in w for w in s.warnings))
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
