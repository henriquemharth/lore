"""Full-text search: accents, prefixes, identifiers, filters, stemming per language and FTS5 syntax protection."""

import unittest
from contextlib import closing

from support import Env, write

from lore.db import connect
from lore.indexer import RunContext
from lore.search import MARK_END, MARK_START, InvalidQuery, build_query, coverage, prefix, search, stem


class TestEnglishStemming(unittest.TestCase):
    def test_plural_and_suffix_folding(self):
        cases = {
            "policies": "policy", "entries": "entry", "classes": "class", "boxes": "box", "matches": "match",
            "indexes": "index", "tokens": "token", "sessions": "session", "uses": "use",
            "indexing": "index", "running": "run", "logging": "log", "indexed": "index", "stopped": "stop",
            "deployed": "deploy", "called": "call",
            "expiration": "expir", "configurations": "configur", "station": "station",
        }
        for word, expected in cases.items():
            with self.subTest(word=word):
                self.assertEqual(expected, stem(word))

    def test_conservative_cases_stay(self):
        for word in ("status", "analysis", "class", "string", "thing", "need", "used", "bus", "api", "Café"):
            with self.subTest(word=word):
                self.assertEqual(word.lower().replace("é", "e"), stem(word))

    def test_codes_and_hashes_are_untouched(self):
        for word in ("DEC-031", "ADR-0007", "audit_log", "06e4b4d", "v2"):
            self.assertEqual(word, stem(word))

    def test_prefix_drops_final_y_so_singular_finds_plural(self):
        self.assertEqual("polic", prefix("policy"))
        self.assertEqual("polic", prefix("policies"))
        self.assertEqual("key", prefix("keys"))  # too short to drop the y

    def test_query_uses_the_prefix(self):
        self.assertEqual('"permission"* AND "tab"*', build_query("permissions per tab").expression)
        self.assertEqual('"polic"*', build_query("Policies").expression)
        self.assertEqual('"index"*', build_query("indexing").expression)


class TestPortugueseStemming(unittest.TestCase):
    def test_portuguese_rules(self):
        q = lambda text: build_query(text, language="pt").expression  # noqa: E731
        self.assertEqual('"permiss"* AND "aba"*', q("permissões por aba"))
        self.assertEqual('"permiss"*', q("Permissão"))
        self.assertEqual('"manuten"*', q("manutenção"))
        self.assertEqual('"expira"*', q("expiração"))
        self.assertEqual('"configura"*', q("configurações"))
        self.assertEqual('"public"*', q("publicar"))
        self.assertEqual("permiss", stem("permissões", "pt"))

    def test_coverage_follows_the_language(self):
        self.assertEqual(1.0, coverage(["permissões"], "a permissão do usuário", "pt"))
        self.assertEqual(1.0, coverage(["permissions"], "the permission of the user", "en"))


class TestBuildQuery(unittest.TestCase):
    def test_phrase_in_quotes_and_identifiers(self):
        self.assertEqual('"order queue"', build_query('"order queue"').expression)
        self.assertEqual('"DEC-031"*', build_query("DEC-031").expression)
        self.assertEqual('"ADR-0007"*', build_query("ADR-0007").expression)
        self.assertEqual('"audit_log"*', build_query("audit_log").expression)

    def test_uppercase_acronym_is_not_a_stop_word(self):
        self.assertEqual('"who"* AND "IT" AND "assign"*', build_query("who in IT assigns").expression)

    def test_fts5_operators_do_not_pass(self):
        for dangerous in ['NEAR(a b)', 'a OR b', 'a* -b', 'col:value', '"', '^start', "a AND (b OR c)"]:
            with self.subTest(dangerous=dangerous):
                try:
                    expression = build_query(dangerous).expression
                except InvalidQuery:
                    continue
                for piece in expression.split(" AND "):
                    self.assertRegex(piece, r'^"[^"]+"\*?$')

    def test_empty_is_refused(self):
        for empty in ["", "   ", "!!!", '""']:
            with self.subTest(empty=empty), self.assertRaises(InvalidQuery):
                build_query(empty)


class TestSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()
        n = cls.env.notes
        write(n, "06-SECURITY.md", "# Security\n\n## Sessions\nThe session ends after 10 min of inactivity.\n\n## Permissions\nMatrix of permissions per tab.\n")
        write(n, "decisions/DEC-031-Order-Queue.md", "# DEC-031 — Order queue\n\nThe dispatcher owns the queue. Published in commit `06e4b4d`.\n")
        write(n, "03-DATABASE.md", "# Database\n\nThe `audit_log` table keeps before and after.\n")
        write(n, "modules/MAINTENANCE.md", "# Maintenance\n\nWork orders and preventive maintenance. Café policies.\n")
        cls.env.indexer().run(RunContext(origin="test"))
        cls.conn = connect(cls.env.db)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.env.close()

    def paths(self, text, **filters):
        return [r["path"] for r in search(self.conn, text, **filters)["results"]]

    def test_accent_and_case_insensitive(self):
        self.assertEqual(["06-SECURITY.md"], self.paths("SESSION inactivity"))
        self.assertIn("modules/MAINTENANCE.md", self.paths("cafe"))

    def test_singular_finds_plural_and_plural_finds_singular(self):
        self.assertIn("06-SECURITY.md", self.paths("permission"))
        self.assertIn("06-SECURITY.md", self.paths("sessions"))
        self.assertIn("modules/MAINTENANCE.md", self.paths("policy"))

    def test_exact_identifiers(self):
        self.assertEqual("decisions/DEC-031-Order-Queue.md", self.paths("DEC-031")[0])
        self.assertEqual(["decisions/DEC-031-Order-Queue.md"], self.paths("06e4b4d"))
        self.assertEqual(["03-DATABASE.md"], self.paths("audit_log"))

    def test_highlighted_excerpt_with_markers_and_position(self):
        r = search(self.conn, "inactivity")["results"][0]
        self.assertIn(f"{MARK_START}inactivity{MARK_END}", r["excerpt"])
        # small document = a single chunk, with the document title
        self.assertEqual("Security", r["title"])
        self.assertEqual(1, r["line_start"])

    def test_without_results_for_all_terms_falls_back_to_any_term(self):
        r = search(self.conn, "inactivity preventive")
        self.assertEqual("any", r["mode"])
        self.assertEqual({"06-SECURITY.md", "modules/MAINTENANCE.md"}, {x["path"] for x in r["results"]})

    def test_filters_by_category_and_source(self):
        self.assertEqual(["decisions/DEC-031-Order-Queue.md"], self.paths("queue", category="Decisions"))
        self.assertEqual([], self.paths("queue", source="other"))
        # a parent category includes its children
        self.assertIn("06-SECURITY.md", self.paths("session", category="Engineering"))

    def test_malicious_input_does_not_break(self):
        for text in ['"; DROP TABLE chunk; --', "NEAR(a b", "a* OR -b", "' OR 1=1", "%_\\"]:
            with self.subTest(text=text):
                try:
                    search(self.conn, text)
                except InvalidQuery:
                    pass
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0], 0)

    def test_removed_document_leaves_the_search(self):
        env = Env()
        try:
            write(env.notes, "a.md", "# A\n\nuniqueword\n")
            idx = env.indexer()
            idx.run(RunContext(origin="test"))
            (env.notes / "a.md").unlink()
            idx.run(RunContext(origin="test"))
            with closing(connect(env.db)) as conn:
                self.assertEqual([], search(conn, "uniqueword")["results"])
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
