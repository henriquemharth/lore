"""Domain wall: an MCP process of one domain never sees the sources of another one.

Two arbitrary domains of the fictional Acme company: ``default`` (the shop) and ``billing``."""

import unittest
from contextlib import closing

from mcp.server.mcpserver.exceptions import ToolError
from support import TAXONOMY, Env, write

from lore.categories import load_taxonomy
from lore.config import PROJECT_ROOT, Settings
from lore.indexer import Indexer, RunContext
from lore.mcp_server import McpLayer, create_server, open_read_only
from lore.sources import ConfigError, load_sources

CLIENT = {"name": "test-suite", "version": "1.0"}


class TestDomainWall(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.billing = self.env.base / "billing"
        self.billing_memories = self.env.base / "memories-billing"
        write(self.env.notes, "shop.md", "# Shop invoicing\n\nThe invoice leaves the shop in commit `abc1234`.\n")
        write(self.env.notes, "decisions/DEC-001-Session.md", "# DEC-001 — Shop session\n\n- **Status:** Accepted\n\nThe session expires in 10 min.\n")
        write(self.billing, "ledger.md", "# Billing ledger\n\nThe invoice of the order shows up in the ledger.\n")
        write(self.billing, "decisions/DEC-001-Ledger.md", "# DEC-001 — Billing ledger\n\n- **Status:** Accepted\n\nSession of the accountant in the ledger.\n")
        self.cfg = self.env.settings()
        # Four sources: notes and MCP memories in the default domain; billing and its memories in "billing".
        self.cfg.sources_file.write_text(
            "domains:\n"
            "  billing:\n    description: of the Acme billing service\n"
            "sources:\n"
            f"  - {{id: notes, name: Notes, type: folder, root: '{self.env.notes}', include: ['**/*.md']}}\n"
            f"  - {{id: memories, name: Shop memories, type: folder, root: '{self.env.memories}', include: ['*.md'],"
            " respect_gitignore: false}\n"
            f"  - {{id: billing, name: Billing, type: folder, root: '{self.billing}', include: ['**/*.md'], domain: billing}}\n"
            f"  - {{id: memories-billing, name: Billing memories, type: folder, root: '{self.billing_memories}',"
            " include: ['*.md'], respect_gitignore: false, domain: billing}\n",
            encoding="utf-8",
        )
        Indexer(self.env.db, load_sources(self.cfg.sources_file), load_taxonomy(TAXONOMY)).run(RunContext(origin="test"))
        self.shop = McpLayer(self.cfg)
        self.billing_mcp = McpLayer(
            self.cfg.model_copy(update={"mcp_domain": "billing", "memories_dir": self.billing_memories})
        )
        self.conn = open_read_only(self.env.db)

    def tearDown(self):
        self.conn.close()
        self.shop.close()
        self.billing_mcp.close()
        self.env.close()

    def sources_of(self, items: list[dict]) -> set[str]:
        return {i["source"].split(":", 1)[0] for i in items}

    def test_search_and_context_only_from_the_own_domain(self):
        for layer, expected in ((self.shop, {"notes"}), (self.billing_mcp, {"billing"})):
            with self.subTest(domain=layer.domain):
                found, _ = layer.search(self.conn, "invoice", 10)
                self.assertEqual(expected, self.sources_of(found["results"]))
                self.assertEqual(0, found["hidden_by_policy"])  # the other domain is not even counted as hidden
                context, _ = layer.get_context(self.conn, "invoice")
                self.assertEqual(expected, self.sources_of(context["chunks"]))
                self.assertEqual(expected, self.sources_of(context["sources"]))

    def test_a_document_of_the_other_domain_does_not_exist(self):
        with closing(open_read_only(self.env.db)) as conn:
            ids = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM document")}
        for layer, foreign in ((self.shop, ids["ledger.md"]), (self.billing_mcp, ids["shop.md"])):
            for reference in (str(foreign), "billing:ledger.md" if layer is self.shop else "notes:shop.md"):
                with self.subTest(domain=layer.domain, reference=reference), self.assertRaises(ToolError) as error:
                    layer.get_document(self.conn, reference, None, None, False)
                self.assertIn("not found", str(error.exception))
        own, _ = self.billing_mcp.get_document(self.conn, str(ids["ledger.md"]), None, None, True)
        self.assertEqual("billing:ledger.md", own["source"])

    def test_decisions_with_the_same_code_do_not_mix(self):
        for layer, title, source in ((self.shop, "Shop session", "notes"), (self.billing_mcp, "Billing ledger", "billing")):
            with self.subTest(domain=layer.domain):
                found, _ = layer.get_decisions(self.conn, None, None, 10)
                self.assertEqual([("DEC-001", title)], [(d["code"], d["title"]) for d in found["decisions"]])
                self.assertEqual(source, found["decisions"][0]["source"].split(":")[0])
                entity, _ = layer.get_entity(self.conn, "DEC-001")
                self.assertTrue(entity["found"])
                expected = f"{source}:decisions/DEC-001-{'Session' if source == 'notes' else 'Ledger'}.md"
                self.assertEqual(expected, entity["node"]["detail"])

    def test_graph_does_not_leak_entities_or_categories_of_the_other_domain(self):
        found, _ = self.shop.get_entity(self.conn, "abc1234")
        self.assertTrue(found["found"])
        foreign, _ = self.billing_mcp.get_entity(self.conn, "abc1234")
        self.assertFalse(foreign["found"])
        g = self.billing_mcp.lore.graph(self.conn, self.billing_mcp.visible_sources)
        self.assertEqual({"billing"}, {n["source_id"] for n in g.nodes.values() if n.get("source_id")})
        self.assertFalse(any(n.get("category") == "Engineering/Database" for n in g.nodes.values()))

    def test_status_counts_only_the_own_domain(self):
        shop, _ = self.shop.status(self.conn)
        billing, _ = self.billing_mcp.status(self.conn)
        self.assertEqual(("default", 2), (shop["domain"], shop["documents"]))
        self.assertEqual(("billing", 2), (billing["domain"], billing["documents"]))
        self.assertEqual("memories-billing", billing["mcp"]["memory"]["source"])

    def test_billing_memory_stays_in_billing(self):
        response, _ = self.billing_mcp.remember(
            self.conn, CLIENT, "Ledger has five states", "The billing ledger has five states, confirmed with the owner.",
            "fact", None, ["conversation with the user"], None,
        )
        self.assertTrue(response["source"].startswith("memories-billing:"))
        self.assertEqual(1, len(list(self.billing_memories.glob("*.md"))))
        self.assertFalse(self.env.memories.exists() and any(self.env.memories.glob("*.md")))

    def test_memory_folder_of_another_domain_is_refused(self):
        # Domain "billing" pointing at the memories folder of the default domain: nothing is written.
        crossed = McpLayer(self.cfg.model_copy(update={"mcp_domain": "billing"}))
        try:
            with self.assertRaises(ToolError) as error:
                crossed.remember(self.conn, CLIENT, "Valid title", "Content with more than twenty characters.",
                                 "fact", None, None, None)
            self.assertIn("domain 'billing'", str(error.exception))
        finally:
            crossed.close()
        self.assertFalse(self.env.memories.exists() and any(self.env.memories.glob("*.md")))

    def test_forget_does_not_reach_the_other_domain(self):
        with closing(open_read_only(self.env.db)) as conn:
            shop_doc = conn.execute("SELECT id FROM document WHERE path = 'shop.md'").fetchone()["id"]
        with self.assertRaises(ToolError) as error:
            self.billing_mcp.forget(self.conn, CLIENT, str(shop_doc), "domain wall test")
        self.assertEqual("Document not found.", str(error.exception))

    def test_a_domain_without_sources_is_refused(self):
        with self.assertRaises(ValueError) as error:
            McpLayer(self.cfg.model_copy(update={"mcp_domain": "finance"}))
        self.assertIn("finance", str(error.exception))

    def test_instructions_use_the_domain_description(self):
        billing = self.billing_mcp.instructions()
        self.assertIn("knowledge base of the Acme billing service.", billing)
        self.assertIn("'billing'", billing)
        shop = self.shop.instructions()
        self.assertIn("knowledge base of this project.", shop)  # no description declared for "default"
        self.assertIn("'default'", shop)
        self.assertIsNotNone(create_server(self.billing_mcp))


class TestDomainSettings(unittest.TestCase):
    def test_default_memories_folder_per_domain(self):
        self.assertEqual(PROJECT_ROOT / "memories", Settings(mcp_domain="default").memories_dir)
        self.assertEqual(PROJECT_ROOT / "memories-billing", Settings(mcp_domain="billing").memories_dir)
        explicit = PROJECT_ROOT / "other"
        self.assertEqual(explicit, Settings(mcp_domain="billing", memories_dir=explicit).memories_dir)

    def test_invalid_domain(self):
        with self.assertRaises(ValueError):
            Settings(mcp_domain="Billing; rm")
        env = Env()
        try:
            file = env.base / "sources.yaml"
            file.write_text(f"sources:\n  - {{id: billing, type: folder, root: '{env.notes}', domain: 'B I L L'}}\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_sources(file)
            file.write_text(f"sources:\n  - {{id: notes, type: folder, root: '{env.notes}'}}\n", encoding="utf-8")
            self.assertEqual("default", load_sources(file).sources[0].domain)  # undeclared: default
            file.write_text(f"sources:\n  - {{id: notes, type: folder, root: '{env.notes}', domain: acme-mobile-app}}\n", encoding="utf-8")
            self.assertEqual("acme-mobile-app", load_sources(file).sources[0].domain)  # any name works
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
