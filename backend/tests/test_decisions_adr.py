"""Architecture Decision Records: numbered files, English statuses (Nygard / MADR styles), supersede relations,
and the Portuguese status words that keep working."""

import unittest
from contextlib import closing

from support import TAXONOMY, Env, write

from lore import graph as graph_module
from lore.categories import load_taxonomy
from lore.db import connect
from lore.decisions import STATES, list_decisions, relevant
from lore.indexer import RunContext
from lore.sources import DecisionRules


class TestAdr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()
        n = cls.env.notes
        # adr-tools / Nygard style: "## Status" section, "Date:" line, numbered title.
        write(n, "docs/adr/0001-record-architecture-decisions.md", """
            # 1. Record architecture decisions

            Date: 2026-01-10

            ## Status

            Accepted

            ## Context

            We need to record the architectural decisions made on the Acme Shop.
            """)
        write(n, "docs/adr/0002-use-postgresql-for-orders.md", """
            # 2. Use PostgreSQL for orders

            Date: 2026-02-01

            ## Status

            Superseded by [ADR-0004](0004-use-an-event-store.md)

            ## Context

            Orders live in PostgreSQL.
            """)
        # MADR style with frontmatter.
        write(n, "docs/adr/0003-cache-the-catalog.md", """
            ---
            status: proposed
            date: 2026-03-01
            ---
            # Cache the catalog

            The catalog is read far more often than it is written.
            """)
        write(n, "docs/adr/0004-use-an-event-store.md", """
            # ADR-0004: Use an event store for orders

            * Status: accepted
            * Date: 2026-04-01

            Supersedes [ADR-0002](0002-use-postgresql-for-orders.md): orders become an event stream.
            """)
        write(n, "docs/adr/0005-graphql-gateway.md", "# GraphQL gateway\n\nStatus: Rejected\n\nToo early for the shop.\n")
        write(n, "docs/adr/0006-soap-api.md", "# SOAP API\n\n## Status\n\nDeprecated\n\nNobody calls it anymore.\n")
        write(n, "docs/adr/0007-service-template.md", """
            # Service template

            Status: Accepted

            This decision replaces ADR-0001 for new services; it also mentions ADR-0003 in passing.
            """)
        write(n, "docs/adr/0009-nightly-export.md", """
            # Nightly export

            Status: Accepted

            This record was superseded by ADR-0010 after the audit.
            """)
        write(n, "docs/adr/0010-streaming-export.md", "# Streaming export\n\nStatus: Accepted\n\nExports stream.\n")
        write(n, "docs/adr/0011-unclear.md", "# Unclear\n\nStatus: Needs verification\n\nTBD.\n")
        # Portuguese words keep working.
        write(n, "decisions/DEC-009-Antiga.md", "# DEC-009 — Antiga\n\n- **Status:** Substituída pela DEC-010\n\nRegra antiga.\n")
        write(n, "decisions/DEC-010-Nova.md", "# DEC-010 — Nova\n\n- **Data:** 2026-05-02\n- **Status:** Aceita\n\nEsta decisão substitui a DEC-009.\n")
        write(n, "decisions/DEC-011-Retirada.md", "# DEC-011 — Ideia\n\n- **Status:** Retirada antes de publicar\n\nNada.\n")
        write(n, "decisions/DEC-012-Proposta.md", "# DEC-012 — Proposta\n\n- **Status:** Proposta\n\nEm discussão.\n")
        cls.env.indexer().run(RunContext(origin="test"))
        with closing(connect(cls.env.db)) as conn:
            cls.decisions = {d["code"]: d for d in list_decisions(conn)}

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_numbered_files_become_adr_codes(self):
        for code in ("ADR-0001", "ADR-0002", "ADR-0003", "ADR-0004", "ADR-0005", "ADR-0006", "ADR-0007", "ADR-0009"):
            self.assertIn(code, self.decisions)
        self.assertEqual("docs/adr/0004-use-an-event-store.md", self.decisions["ADR-0004"]["path"])
        self.assertEqual(4, self.decisions["ADR-0004"]["number"])

    def test_titles_drop_the_code_and_the_number(self):
        self.assertEqual("Record architecture decisions", self.decisions["ADR-0001"]["title"])
        self.assertEqual("Use an event store for orders", self.decisions["ADR-0004"]["title"])
        self.assertEqual("Cache the catalog", self.decisions["ADR-0003"]["title"])

    def test_english_statuses(self):
        states = {code: d["state"] for code, d in self.decisions.items()}
        self.assertEqual("active", states["ADR-0004"])
        self.assertEqual("proposed", states["ADR-0003"])
        self.assertEqual("deprecated", states["ADR-0005"])  # rejected
        self.assertEqual("deprecated", states["ADR-0006"])
        self.assertEqual("active", states["ADR-0010"])
        self.assertEqual("undeclared", states["ADR-0011"])
        self.assertTrue(set(states.values()) <= set(STATES))

    def test_status_and_date_sources(self):
        self.assertEqual("Accepted", self.decisions["ADR-0001"]["declared_status"])  # "## Status" section
        self.assertEqual("2026-01-10", self.decisions["ADR-0001"]["date"])  # "Date:" line
        self.assertEqual("proposed", self.decisions["ADR-0003"]["declared_status"])  # frontmatter
        self.assertEqual("2026-03-01", self.decisions["ADR-0003"]["date"])
        self.assertEqual("accepted", self.decisions["ADR-0004"]["declared_status"])  # "* Status:" list
        self.assertEqual("2026-05-02", self.decisions["DEC-010"]["date"])  # Portuguese "Data"

    def test_superseded_by_in_the_status(self):
        d = self.decisions["ADR-0002"]
        self.assertEqual("superseded", d["state"])
        self.assertEqual(["ADR-0004"], [s["code"] for s in d["superseded_by"]])  # status + "Supersedes" text, once

    def test_supersedes_written_by_a_later_decision(self):
        d = self.decisions["ADR-0001"]
        self.assertEqual("superseded", d["state"])  # ADR-0007 writes "replaces ADR-0001"
        [by] = d["superseded_by"]
        self.assertEqual("ADR-0007", by["code"])
        self.assertIn("replaces ADR-0001", by["excerpt"])
        self.assertIsInstance(by["line"], int)

    def test_superseded_by_written_in_the_record_itself(self):
        d = self.decisions["ADR-0009"]
        self.assertEqual("superseded", d["state"])
        self.assertEqual("ADR-0010", d["superseded_by"][0]["code"])
        self.assertEqual("active", self.decisions["ADR-0010"]["state"])  # the passive voice does not flip it

    def test_a_mere_mention_is_only_a_later_citation(self):
        d = self.decisions["ADR-0003"]
        self.assertEqual("proposed", d["state"])
        self.assertEqual(["ADR-0007"], [c["code"] for c in d["cited_by_later_decisions"]])

    def test_portuguese_statuses_still_work(self):
        self.assertEqual("superseded", self.decisions["DEC-009"]["state"])
        self.assertEqual(["DEC-010"], [s["code"] for s in self.decisions["DEC-009"]["superseded_by"]])
        self.assertEqual("active", self.decisions["DEC-010"]["state"])
        self.assertEqual("deprecated", self.decisions["DEC-011"]["state"])
        self.assertEqual("proposed", self.decisions["DEC-012"]["state"])

    def test_adr_documents_are_decision_nodes_in_the_graph(self):
        with closing(connect(self.env.db)) as conn:
            g = graph_module.build(conn, load_taxonomy(TAXONOMY))
            ids = {d["path"]: f"doc:{d['id']}" for d in conn.execute("SELECT id, path FROM document")}
            listed = relevant(conn, None, limit=100)
        node = g.nodes[ids["docs/adr/0004-use-an-event-store.md"]]
        self.assertEqual(("decision", "ADR-0004"), (node["type"], node["code"]))
        edges = {(e["type"], e["from"], e["to"]) for e in g.edges.values()}
        self.assertIn(
            ("mentions", ids["docs/adr/0007-service-template.md"], ids["docs/adr/0001-record-architecture-decisions.md"]),
            edges,
        )
        codes = [d["code"] for d in listed["decisions"]]
        self.assertEqual(codes.index("ADR-0001") < codes.index("ADR-0002"), True)

    def test_custom_patterns_replace_the_defaults(self):
        rules = DecisionRules(patterns=(r"RFC-\d{3}",), numbered_folders=())
        self.assertEqual("RFC-012", rules.code_from_path("rfcs/RFC-012-caching.md"))
        self.assertIsNone(rules.code_from_path("docs/adr/0001-x.md"))
        self.assertEqual(["RFC-012"], rules.find_in_text("See RFC-012 and ADR-0001."))
        self.assertTrue(rules.is_code("RFC-123"))
        self.assertFalse(rules.is_code("DEC-123"))


if __name__ == "__main__":
    unittest.main()
