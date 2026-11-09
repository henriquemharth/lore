"""API over real HTTP: routes, read-only, security headers, local Host and web UI."""

import json
import unittest
from urllib.parse import quote

from support import Env, Server, write

from lore.api import create_app, write_routes
from lore.config import Settings

CONFIRM = {"X-Lore-Confirm": "reindex"}


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()
        n = cls.env.notes
        write(n, "00-INDEX.md", "# Index\n\nSee [[06-SECURITY]].\n")
        write(n, "06-SECURITY.md", "---\ndescription: access rules\n---\n# Security\n\n## Sessions\nInactivity of 10 min.\n")
        write(n, "decisions/DEC-042-Center.md", "# DEC-042 — Center\n\n- **Date:** 2026-09-23\n\nAudit.\n")
        frontend = cls.env.base / "dist"
        write(frontend, "index.html", "<!doctype html><title>lore</title><div id=root></div>")
        write(frontend, "assets/app.js", "console.log('ok')")
        cls.app = create_app(cls.env.settings(frontend=frontend), sync=False)
        cls.server = Server(cls.app).__enter__()
        status, _, body = cls.server.request("POST", "/api/lore/reindex", headers=CONFIRM)
        assert status == 200, body

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__()
        cls.env.close()

    def get(self, path, **kw):
        return self.server.request("GET", path, **kw)

    # -------------------------------------------------------------- routes

    def test_status(self):
        status, headers, s = self.get("/api/lore/status")
        self.assertEqual(200, status)
        self.assertTrue(s["read_only"])
        self.assertEqual(3, s["documents"]["total"])
        self.assertGreater(s["chunks"], 0)
        self.assertEqual("completed", s["last_run"]["status"])
        self.assertEqual("api", s["last_run"]["origin"])
        self.assertEqual([], s["errors"])
        self.assertEqual([], s["pending"])
        categories = {c["category"] for c in s["categories"]}
        self.assertTrue({"Decisions", "Engineering/Security", "Engineering"} <= categories)
        self.assertFalse(s["search"]["semantic"]["available"])
        self.assertEqual("en", s["search"]["language"])
        self.assertEqual("default", s["sources"][0]["domain"])
        self.assertEqual([{"name": "default", "description": None, "sources": ["notes"]}], s["domains"])
        self.assertEqual("no-store", headers["cache-control"])

    def test_document_list_and_detail(self):
        _, _, listing = self.get("/api/lore/documents")
        self.assertEqual(3, listing["total"])
        doc = next(d for d in listing["documents"] if d["path"] == "06-SECURITY.md")
        for field in ("path", "name", "size", "hash", "modified_at", "category", "status", "indexed_at", "pending"):
            self.assertIn(field, doc)
        status, _, det = self.get(f"/api/lore/documents/{doc['id']}")
        self.assertEqual(200, status)
        self.assertEqual("Security", det["title"])
        self.assertEqual({"description": "access rules"}, det["frontmatter"])
        self.assertEqual(["Security", "Sessions"], [h["text"] for h in det["headings"]])
        self.assertTrue(det["chunks"])
        self.assertIn("position_percent", det["chunks"][0])
        self.assertIn("Inactivity", det["content"])
        self.assertEqual("00-INDEX.md", det["links_in"][0]["path"])
        self.assertEqual(1, len(det["versions"]))
        self.assertEqual("Acme Shop notes", det["source_name"])
        self.assertNotIn("identity", det)

    def test_list_filters(self):
        _, _, r = self.get("/api/lore/documents?category=Decisions")
        self.assertEqual(["decisions/DEC-042-Center.md"], [d["path"] for d in r["documents"]])
        _, _, r = self.get("/api/lore/documents?q=SECURITY")
        self.assertEqual(1, r["total"])
        _, _, r = self.get("/api/lore/documents?source=notes&limit=1&offset=1")
        self.assertEqual((3, 1), (r["total"], len(r["documents"])))
        self.assertEqual(422, self.get("/api/lore/documents?status=indexado")[0])

    def test_missing_document_and_invalid_id(self):
        self.assertEqual(404, self.get("/api/lore/documents/999999")[0])
        for path in ("/api/lore/documents/..%2F..%2Fetc%2Fpasswd", "/api/lore/documents/abc", "/..%2F..%2Fetc%2Fpasswd"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertIn(status, (404, 422))
                self.assertNotIn("root:", str(body))

    def test_search(self):
        status, _, r = self.get("/api/lore/search?q=inactivity")
        self.assertEqual(200, status)
        self.assertEqual("06-SECURITY.md", r["results"][0]["path"])
        self.assertEqual("text", r["used_mode"])
        self.assertEqual({"start": "\x02", "end": "\x03"}, r["markers"])
        _, _, r = self.get("/api/lore/search?q=center&mode=hybrid")
        self.assertIn("warning", r)
        self.assertEqual(422, self.get("/api/lore/search?q=%21%21")[0])
        self.assertEqual(422, self.get("/api/lore/search")[0])
        self.assertEqual(422, self.get("/api/lore/search?q=x&mode=semantic")[0])

    def test_runs_and_audit(self):
        _, _, runs = self.get("/api/lore/runs")
        self.assertEqual("api", runs[0]["origin"])
        self.assertIsInstance(runs[0]["full"], bool)
        _, _, entries = self.get("/api/lore/audit")
        self.assertIn("reindex_completed", [a["action"] for a in entries])
        self.assertEqual("127.0.0.1", entries[0]["ip"])

    def test_openapi(self):
        status, _, spec = self.get("/api/lore/openapi.json")
        self.assertEqual(200, status)
        self.assertIn("/api/lore/status", spec["paths"])

    # -------------------------------------------------------------- read only

    def test_the_only_write_route_is_reindex(self):
        self.assertEqual([("POST", "/api/lore/reindex")], write_routes(self.app))

    def test_write_methods_on_read_routes_are_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/lore/documents/1", "/api/lore/documents", "/api/lore/status"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(405, self.server.request(method, path, body={"content": "x"})[0])

    def test_reindex_requires_the_confirmation_header(self):
        status, _, body = self.server.request("POST", "/api/lore/reindex", body={"full": True})
        self.assertEqual(403, status)
        self.assertIn("X-Lore-Confirm", body["detail"])
        status, _, body = self.server.request("POST", "/api/lore/reindex", body={"full": True}, headers=CONFIRM)
        self.assertEqual(200, status)
        self.assertEqual(3, body["totals"]["reprocessed"])
        self.assertTrue(body["full"])

    def test_reindex_does_not_change_the_sources(self):
        before = {p: p.read_bytes() for p in self.env.notes.rglob("*.md")}
        self.server.request("POST", "/api/lore/reindex", body={"full": True}, headers=CONFIRM)
        self.assertEqual(before, {p: p.read_bytes() for p in self.env.notes.rglob("*.md")})

    # -------------------------------------------------------------- security

    def test_non_local_host_is_refused(self):
        for host in ("evil.example", "192.0.2.10:8610", "app.acme.example"):
            with self.subTest(host=host):
                status, _, _ = self.get("/api/lore/status", headers={"Host": host})
                self.assertEqual(400, status)
        self.assertEqual(200, self.get("/api/lore/status", headers={"Host": "localhost:8610"})[0])

    def test_no_cors_and_security_headers(self):
        _, headers, _ = self.get("/api/lore/status", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertEqual("nosniff", headers["x-content-type-options"])
        self.assertEqual("DENY", headers["x-frame-options"])
        status, headers, _ = self.server.request(
            "OPTIONS",
            "/api/lore/reindex",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "x-lore-confirm"},
        )
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertNotEqual(200, status)

    # -------------------------------------------------------------- web UI

    def test_ui_and_react_routes(self):
        status, headers, html = self.get("/")
        self.assertEqual(200, status)
        self.assertIn("<title>lore</title>", html)
        self.assertEqual("no-cache", headers.get("cache-control"))  # rebuilding the UI never leaves a stale page
        status, js_headers, _ = self.get("/assets/app.js")
        self.assertEqual(200, status)
        self.assertNotIn("cache-control", js_headers)
        # a React route (no extension) returns index.html
        status, headers, html = self.get("/explorer/12")
        self.assertEqual(200, status)
        self.assertIn("<div id=root>", html)
        self.assertEqual("no-cache", headers.get("cache-control"))
        self.assertEqual(404, self.get("/assets/missing.js")[0])
        self.assertEqual(404, self.get("/api/lore/missing")[0])


class TestSettings(unittest.TestCase):
    def test_only_listens_on_a_local_address(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(host, Settings(http_host=host).http_host)
        for host in ("0.0.0.0", "192.0.2.20", "::", "example.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                Settings(http_host=host)

    def test_sync_interval(self):
        self.assertEqual(0, Settings(sync_seconds=0).sync_seconds)
        with self.assertRaises(ValueError):
            Settings(sync_seconds=5)

    def test_search_language(self):
        self.assertEqual("en", Settings().search_language)
        self.assertEqual("pt", Settings(search_language="pt").search_language)
        with self.assertRaises(ValueError):
            Settings(search_language="fr")

    def test_environment_variables_use_the_lore_prefix(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"LORE_HTTP_PORT": "8711", "LORE_MCP_MAX_CHUNKS": "3", "LORE_MCP_DOMAIN": "billing"}):
            cfg = Settings()
        self.assertEqual((8711, 3, "billing"), (cfg.http_port, cfg.mcp_max_chunks, cfg.mcp_domain))
        self.assertTrue(str(cfg.memories_dir).endswith("memories-billing"))
        self.assertEqual("lore.sqlite3", cfg.database.name)


class TestApiKnowledge(unittest.IsolatedAsyncioTestCase):
    """Read routes for the graph, decisions, context and the MCP panel."""

    @classmethod
    def setUpClass(cls):
        from test_mcp import PRIVACY, build_notes

        cls.env = Env()
        build_notes(cls.env)
        cls.cfg = cls.env.settings(privacy=PRIVACY)
        cls.app = create_app(cls.cfg, sync=False)
        cls.server = Server(cls.app).__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__()
        cls.env.close()

    def get(self, path):
        return self.server.request("GET", path)

    def test_graph_every_relation_has_evidence(self):
        status, _, g = self.get("/api/lore/graph")
        self.assertEqual(200, status)
        self.assertTrue(g["edges"])
        self.assertTrue(all(e["total_evidence"] >= 1 for e in g["edges"]))
        self.assertTrue({e["type"] for e in g["edges"]} <= {"subcategory", "category", "link", "mentions"})
        self.assertEqual(["subcategory", "category", "link", "mentions"], g["types"]["edges"])
        self.assertNotIn("evidence", g["edges"][0])  # evidence comes on demand
        edge = next(e for e in g["edges"] if e["type"] == "link")
        status, _, det = self.get(f"/api/lore/graph/edge?id={quote(edge['id'])}")
        self.assertEqual(200, status)
        self.assertTrue(det["edge"]["evidence"][0]["line"])
        self.assertIn("wikilink", det["edge"]["evidence"][0]["rule"])
        self.assertEqual(edge["from"], det["from"]["id"])
        self.assertEqual(404, self.get("/api/lore/graph/edge?id=invented")[0])

    def test_graph_node_with_relations_and_chunks(self):
        _, _, g = self.get("/api/lore/graph")
        dec = next(n for n in g["nodes"] if n.get("code") == "DEC-001")
        status, _, det = self.get(f"/api/lore/graph/node?id={quote(dec['id'])}")
        self.assertEqual(200, status)
        self.assertTrue(det["relations"])
        self.assertIn(det["relations"][0]["direction"], ("in", "out"))
        self.assertTrue(det["own_chunks"])
        self.assertEqual(dec["document_id"], det["open"]["document_id"])
        self.assertEqual(404, self.get("/api/lore/graph/node?id=doc%3A999999")[0])

    def test_decisions_and_context(self):
        _, _, r = self.get("/api/lore/decisions?q=session")
        states = {d["code"]: d["state"] for d in r["decisions"]}
        self.assertEqual("superseded", states["DEC-001"])
        _, _, only = self.get("/api/lore/decisions?state=deprecated")
        self.assertEqual(["DEC-003"], [d["code"] for d in only["decisions"]])
        self.assertEqual(200, self.get("/api/lore/decisions?state=proposed")[0])
        self.assertEqual(422, self.get("/api/lore/decisions?state=invented")[0])
        status, _, c = self.get("/api/lore/context?task=Change%20the%20user%20session%20expiration")
        self.assertEqual(200, status)
        self.assertTrue(c["chunks"])
        self.assertFalse(any("hr/payroll.md" in x["source"] for x in c["chunks"]))
        self.assertEqual(422, self.get("/api/lore/context?task=%21%21%21")[0])

    async def test_mcp_panel_without_secrets(self):
        from mcp import Client
        from mcp.types import Implementation

        from lore.mcp_server import McpLayer, create_server

        layer = McpLayer(self.cfg)
        try:
            async with Client(create_server(layer), client_info=Implementation(name="panel-test", version="3")) as client:
                await client.call_tool("lore_search", {"query": "session TOKEN_API=abcdef0123456789secret"})
        finally:
            layer.close()
        status, _, p = self.get("/api/lore/mcp")
        self.assertEqual(200, status)
        self.assertFalse(p["online"])  # no live MCP process now
        self.assertFalse(p["read_only"])  # lore_remember is on by default (it only creates new memories)
        self.assertEqual(8, len(p["tools"]))
        self.assertTrue(p["memories"]["allowed"])
        self.assertIsNone(p["memories"]["source"])  # this environment has no memories source
        self.assertEqual([], p["memories"]["recent"])
        self.assertTrue(p["command"].endswith("lore-mcp"))
        self.assertEqual("panel-test 3", p["recent_queries"][0]["client"])
        self.assertEqual("lore_search", p["recent_queries"][0]["tool"])
        self.assertIn("[REDACTED", p["recent_queries"][0]["query"])
        self.assertNotIn("abcdef0123456789secret", json.dumps(p))
        self.assertGreaterEqual(p["usage"]["queries"], 1)
        self.assertEqual(8, p["limits"]["max_chunks"])

    def test_still_no_new_write_route(self):
        self.assertEqual([("POST", "/api/lore/reindex")], write_routes(self.app))


if __name__ == "__main__":
    unittest.main()
