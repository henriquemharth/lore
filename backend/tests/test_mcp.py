"""MCP server: read-only tools, limits, privacy, traceability and audit."""

import json
import os
import sqlite3
import sys
import unittest
from contextlib import closing

from mcp import Client, StdioServerParameters
from mcp.types import Implementation
from support import Env, write

from lore import audit
from lore.db import connect
from lore.indexer import RunContext
from lore.mcp_server import McpLayer, create_server, open_read_only

READ = {"lore_status", "lore_search", "lore_get_context", "lore_get_document", "lore_get_decisions", "lore_get_entity"}
WRITE = {"lore_remember", "lore_forget"}
TOOLS = READ | WRITE
PRIVACY = "deny: []\nsensitive:\n  - sources: [notes]\n    paths: ['hr/*.md']\n    reason: employee data\n"
CLIENT = Implementation(name="test-suite", version="1.2")


def build_notes(env: Env) -> None:
    n = env.notes
    write(n, "00-INDEX.md", "# Index\n\n- [[06-SECURITY]]\n- [[decisions/DEC-001-Session]]\n")
    sections = "\n\n".join(
        f"## Sessions part {i}\n\n" + ("The user session expires after inactivity and the permission is reviewed. " * 12)
        for i in range(1, 7)
    )
    write(n, "06-SECURITY.md", "# Security\n\n" + sections + "\n\n## Permissions\n\nMatrix of permissions per tab and session.\n")
    write(n, "decisions/DEC-001-Session.md", "# DEC-001 — Short session\n\n- **Date:** 2026-09-01\n- **Status:** Accepted\n\nThe session expires in 10 min.\n")
    write(n, "decisions/DEC-002-New-Session.md", "# DEC-002 — New session\n\n- **Status:** Accepted\n\nSupersedes DEC-001 for the session.\n")
    write(n, "decisions/DEC-003-Withdrawn.md", "# DEC-003 — Withdrawn\n\n- **Status:** Withdrawn before release\n\nEternal session.\n")
    write(n, "hr/payroll.md", "# Payroll\n\nSalary of John Doe: HR session. See [[06-SECURITY]].\n")
    write(n, "notes/config.md", "# Config\n\nFor the local session use SHOP_JWT_SECRET=abcdefghijklmnopqrstuvwxyz0123 in .env.\n")
    env.indexer().run(RunContext(origin="test"))


class TestMcpTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = Env()
        build_notes(self.env)
        self.cfg = self.env.settings(
            privacy=PRIVACY,
            mcp_max_chunks=3,
            mcp_max_chars_per_chunk=300,
            mcp_max_chars_total=1000,
            mcp_max_documents=2,
            mcp_min_score=0.2,
            mcp_max_search_results=5,
        )
        self.layer = McpLayer(self.cfg)

    def tearDown(self):
        self.layer.close()
        self.env.close()

    async def call(self, tool: str, arguments: dict | None = None) -> tuple[bool, object]:
        async with Client(create_server(self.layer), client_info=CLIENT) as client:
            r = await client.call_tool(tool, arguments or {})
        text = r.content[0].text
        try:
            return r.is_error, json.loads(text)
        except ValueError:
            return r.is_error, text

    def audit_entries(self) -> list[dict]:
        with closing(connect(self.env.db)) as conn:
            return [r for r in audit.list_entries(conn, 100) if r["origin"] == "mcp"]

    # ------------------------------------------------------------------ read only

    async def test_only_remember_and_forget_write_and_nothing_edits_or_deletes_for_good(self):
        async with Client(create_server(self.layer), client_info=CLIENT) as client:
            tools = (await client.list_tools()).tools
        self.assertEqual(TOOLS, {t.name for t in tools})
        for t in tools:
            self.assertEqual(t.name not in WRITE, t.annotations.read_only_hint, t.name)
            self.assertEqual(t.name == "lore_forget", t.annotations.destructive_hint, t.name)  # only it takes something out
            self.assertFalse(any(p in t.name for p in ("update", "delete", "remove", "move", "write", "edit")))

    def test_database_refuses_writes_outside_the_audit_log(self):
        conn = open_read_only(self.env.db)
        try:
            for sql in ("DELETE FROM document", "UPDATE document SET title = 'x'", "INSERT INTO chunk (id) VALUES (1)",
                        "DROP TABLE link", "PRAGMA user_version = 99", "ATTACH DATABASE ':memory:' AS x"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(sql)
            audit.record(conn, "mcp_query", "mcp", {"test": True})  # the only write allowed
        finally:
            conn.close()

    # ------------------------------------------------------------------ tools

    async def test_status(self):
        error, s = await self.call("lore_status")
        self.assertFalse(error)
        self.assertTrue(s["lore_available"])
        self.assertEqual("default", s["domain"])
        self.assertTrue(s["database"]["available"])
        self.assertEqual(6, s["documents"])  # 7 indexed, 1 sensitive hidden
        self.assertEqual(1, s["documents_hidden_by_policy"])
        self.assertEqual("up_to_date", s["index_state"])
        self.assertIsNotNone(s["last_run"]["finished_at"])
        self.assertNotIn(str(self.env.base), json.dumps(s))  # no local paths

    async def test_search_with_origin_and_limit(self):
        error, r = await self.call("lore_search", {"query": "session", "limit": 999})
        self.assertFalse(error)
        self.assertLessEqual(len(r["results"]), 5)
        first = r["results"][0]
        for field in ("content", "source", "heading", "document_id", "chunk_id", "chunk_index", "score", "lines"):
            self.assertIn(field, first)
        self.assertTrue(0 < first["score"] <= 1)
        self.assertFalse(any("hr/payroll.md" in i["source"] for i in r["results"]))
        self.assertGreaterEqual(r["hidden_by_policy"], 1)

    async def test_search_without_results(self):
        error, r = await self.call("lore_search", {"query": "xyzzy qwerty"})
        self.assertFalse(error)
        self.assertEqual([], r["results"])
        self.assertEqual("No chunk found.", r["note"])

    async def test_get_context_respects_limits_and_says_what_was_left_out(self):
        error, r = await self.call("lore_get_context", {"task": "Change the user session expiration"})
        self.assertFalse(error)
        self.assertLessEqual(len(r["chunks"]), 3)
        self.assertLessEqual(len(r["sources"]), 2)
        self.assertTrue(all(len(c["content"]) <= 301 for c in r["chunks"]))
        self.assertLessEqual(sum(len(c["content"]) for c in r["chunks"]), 1000)
        self.assertTrue(r["more_available"])
        self.assertIn("left out", r["note"])
        for c in r["chunks"]:
            for field in ("content", "source", "heading", "document_id", "chunk_id", "score"):
                self.assertIn(field, c)
        states = {d["code"]: d["state"] for d in r["decisions"]}
        self.assertEqual("superseded", states.get("DEC-001"))
        self.assertFalse(any("hr/payroll.md" in c["source"] for c in r["chunks"]))
        self.assertIn("by_privacy", r["omitted"])

    async def test_get_context_without_results(self):
        error, r = await self.call("lore_get_context", {"task": "xyzzy qwerty"})
        self.assertFalse(error)
        self.assertEqual([], r["chunks"])
        self.assertEqual(0, r["returned"]["characters"])

    async def test_get_document_by_parts(self):
        _, found = await self.call("lore_search", {"query": "Permissions matrix"})
        doc_id = next(i["document_id"] for i in found["results"] if i["source"] == "notes:06-SECURITY.md")
        error, r = await self.call("lore_get_document", {"document_id": str(doc_id)})
        self.assertFalse(error)
        self.assertEqual([], r["chunks"])  # large document: outline only
        self.assertGreater(len(r["chunks_outline"]), 1)
        _, part = await self.call("lore_get_document", {"document_id": f"doc:{doc_id}", "chunks": [0]})
        self.assertEqual([0], [c["chunk_index"] for c in part["chunks"]])
        _, by_heading = await self.call("lore_get_document", {"document_id": "notes:06-SECURITY.md", "heading": "Permissions"})
        self.assertTrue(by_heading["chunks"])
        self.assertTrue(all("Permissions" in c["heading"] for c in by_heading["chunks"]))
        _, whole = await self.call("lore_get_document", {"document_id": str(doc_id), "full": True})
        self.assertLessEqual(sum(len(c["content"]) for c in whole["chunks"]), 1000)
        self.assertTrue(whole["omitted_chunks_over_limit"])
        _, small = await self.call("lore_get_document", {"document_id": "notes:decisions/DEC-001-Session.md"})
        self.assertTrue(small["chunks"])  # a small one comes whole

    async def test_get_document_refuses_traversal_invalid_and_sensitive(self):
        for identifier in ("../../etc/passwd", "notes:../06-SECURITY.md", "/etc/passwd", "abc", "999999", "notes:hr/payroll.md"):
            with self.subTest(identifier=identifier):
                error, text = await self.call("lore_get_document", {"document_id": identifier})
                self.assertTrue(error)
                self.assertNotIn("root:", str(text))
                self.assertNotIn("Salary", str(text))

    async def test_secret_in_the_document_is_redacted(self):
        error, r = await self.call("lore_get_document", {"document_id": "notes:notes/config.md"})
        self.assertFalse(error)
        text = json.dumps(r, ensure_ascii=False)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", text)
        self.assertIn("[REDACTED: secret value]", text)
        self.assertGreaterEqual(self.audit_entries()[0]["details"]["secrets_redacted"], 1)

    async def test_get_decisions_with_state(self):
        error, r = await self.call("lore_get_decisions", {"query": "session"})
        self.assertFalse(error)
        states = {d["code"]: d["state"] for d in r["decisions"]}
        self.assertEqual({"DEC-001": "superseded", "DEC-002": "active", "DEC-003": "deprecated"}, states)
        dec1 = next(d for d in r["decisions"] if d["code"] == "DEC-001")
        self.assertEqual("DEC-002", dec1["superseded_by"][0]["code"])
        self.assertIn("proposed", r["states"])
        _, only_active = await self.call("lore_get_decisions", {"query": "session", "state": "active"})
        self.assertEqual(["DEC-002"], [d["code"] for d in only_active["decisions"]])
        error, _ = await self.call("lore_get_decisions", {"state": "invented"})
        self.assertTrue(error)

    async def test_get_entity(self):
        error, r = await self.call("lore_get_entity", {"entity": "DEC-001"})
        self.assertFalse(error)
        self.assertTrue(r["found"])
        self.assertEqual("decision", r["node"]["type"])
        self.assertTrue(all(rel["evidence"] or rel["evidence_count"] for rel in r["relations"]))
        self.assertTrue(all(rel["direction"] in ("in", "out") for rel in r["relations"]))
        error, sec = await self.call("lore_get_entity", {"entity": "06-SECURITY"})
        others = {rel["other"]["name"] for rel in sec["relations"]}
        self.assertNotIn("Payroll", others)  # the sensitive document pointing at it does not show up
        self.assertGreaterEqual(sec["hidden_by_policy"], 1)
        error, nothing = await self.call("lore_get_entity", {"entity": "Authentication"})
        self.assertFalse(error)
        self.assertFalse(nothing["found"])
        self.assertIn("lore_get_context", nothing["note"])

    async def test_empty_input_is_refused(self):
        for tool, arguments in (("lore_search", {"query": "  "}), ("lore_get_context", {"task": ""}), ("lore_get_entity", {"entity": ""})):
            with self.subTest(tool=tool):
                error, _ = await self.call(tool, arguments)
                self.assertTrue(error)

    def test_instructions_name_the_project(self):
        text = self.layer.instructions()
        self.assertTrue(text.startswith("lore: local, read-only knowledge base of this project."))
        self.assertIn("'default'", text)

    # ------------------------------------------------------------------ audit

    async def test_audit_records_query_client_ids_size_and_error(self):
        await self.call("lore_search", {"query": "session API_TOKEN=verySecretValue99"})
        await self.call("lore_get_document", {"document_id": "../../etc/passwd"})
        entries = self.audit_entries()
        self.assertEqual(2, len(entries))
        failed, found = entries[0]["details"], entries[1]["details"]
        self.assertEqual("lore_search", found["tool"])
        self.assertEqual("default", found["domain"])
        self.assertEqual({"name": "test-suite", "version": "1.2"}, found["client"])
        self.assertEqual("test-suite 1.2", entries[1]["agent"])
        self.assertEqual("mcp_query", entries[1]["action"])
        self.assertNotIn("verySecretValue99", json.dumps(found))
        self.assertIn("[REDACTED", found["input"]["query"])
        self.assertTrue(found["documents"] and found["chunks"])
        self.assertGreater(found["response_characters"], 0)
        self.assertGreaterEqual(found["duration_ms"], 0)
        self.assertEqual("ok", found["result"])
        self.assertEqual("lore_get_document", failed["tool"])
        self.assertEqual("error", failed["result"])
        self.assertIn("not found", failed["error"])


class TestUnavailable(unittest.IsolatedAsyncioTestCase):
    """Without the index file the MCP server answers with a clear error."""

    async def test_missing_and_corrupted_index(self):
        env = Env()
        try:
            env.data.mkdir()
            layer = McpLayer(env.settings())
            async with Client(create_server(layer), client_info=CLIENT) as client:
                r = await client.call_tool("lore_status", {})
                self.assertTrue(r.is_error)
                self.assertIn("lore index was not found", r.content[0].text)
                env.db.write_bytes(b"this is not an sqlite database" * 100)
                r = await client.call_tool("lore_search", {"query": "session"})
                self.assertTrue(r.is_error)
                self.assertIn("could not be read", r.content[0].text)
            pending = (env.data / "mcp-audit-pending.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(2, len(pending))
            self.assertEqual("error", json.loads(pending[0])["result"])
            self.assertFalse((env.data / "lore.sqlite3-wal").exists())
            layer.close()
        finally:
            env.close()


class TestStdioProcess(unittest.IsolatedAsyncioTestCase):
    """The real server, the way an AI client starts it: a child process talking over stdio."""

    async def test_initializes_lists_tools_and_answers(self):
        env = Env()
        try:
            build_notes(env)
            cfg = env.settings(privacy=PRIVACY)
            process_env = {**os.environ, **env.process_env(cfg)}
            params = StdioServerParameters(command=sys.executable, args=["-m", "lore.mcp_server"], env=process_env)
            async with Client(params, client_info=CLIENT) as client:
                tools = {t.name for t in (await client.list_tools()).tools}
                r = await client.call_tool("lore_status", {})
            self.assertEqual(TOOLS, tools)
            self.assertFalse(r.is_error)
            self.assertEqual(6, json.loads(r.content[0].text)["documents"])
            # the process registered its heartbeat and removed it afterwards
            self.assertEqual([], list((env.data / "mcp").glob("*.json")))
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
