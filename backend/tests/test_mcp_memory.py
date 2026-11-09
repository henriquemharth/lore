"""lore_remember / lore_forget: the only MCP writes — they only create new memories or move them to a trash."""

import json
import os
import re
import socket
import sqlite3
import stat
import sys
import unittest
from contextlib import closing
from pathlib import Path

import yaml
from mcp import Client, StdioServerParameters
from mcp.types import Implementation
from support import Env, Server, write

from lore import audit
from lore.api import create_app
from lore.db import connect
from lore.indexer import RunContext
from lore.mcp_server import McpLayer, create_server
from lore.memories import TRASH
from lore.service import Lore

CLIENT = Implementation(name="claude-code", version="2.1.231")
NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[a-z0-9-]+-[0-9a-f]{12}\.md$")
MEMORY = {
    "title": "Order queue: dispatcher assigns",
    "content": "A newly opened work order lands unassigned in the dispatcher queue, who assigns it to a technician. Confirmed with the product owner.",
    "kind": "fact",
    "subject": "Maintenance",
    "evidence": ["notes:decisions/DEC-034-Queue.md", "commit `06e4b4d`"],
    "related_decisions": ["dec-034", "ADR-0007"],
}


def snapshot(folder: Path) -> dict[str, tuple[str, int, int]]:
    """Content, mtime and mode of every file (proof that nothing existing changed)."""
    return {
        str(p.relative_to(folder)): (p.read_text(encoding="utf-8"), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


class McpMemoryBase(unittest.IsolatedAsyncioTestCase):
    layer: McpLayer

    async def call(self, tool: str, arguments: dict, layer: McpLayer | None = None) -> tuple[bool, object]:
        async with Client(create_server(layer or self.layer), client_info=CLIENT) as client:
            r = await client.call_tool(tool, arguments)
        text = r.content[0].text
        try:
            return r.is_error, json.loads(text)
        except ValueError:
            return r.is_error, text

    def files(self) -> list[Path]:
        return sorted(self.env.memories.glob("*.md")) if self.env.memories.exists() else []

    def index(self) -> None:
        Lore(self.cfg).indexer.run(RunContext(origin="test"))


class TestRemember(McpMemoryBase):
    def setUp(self):
        self.env = Env()
        write(self.env.notes, "decisions/DEC-034-Queue.md", "# DEC-034 — Dispatcher queue\n\n- **Status:** Accepted\n\nAn open order goes to the dispatcher.\n")
        write(self.env.notes, "06-SECURITY.md", "# Security\n\nThe session expires after inactivity.\n")
        self.cfg = self.env.settings(memories=True)
        self.index()
        self.layer = McpLayer(self.cfg)

    def tearDown(self):
        self.layer.close()
        self.env.close()

    def memory_audit(self) -> list[dict]:
        with closing(connect(self.env.db)) as conn:
            return [r for r in audit.list_entries(conn, 100) if r["action"] == "mcp_memory"]

    # ------------------------------------------------------------------ creation

    async def test_creates_a_new_unverified_file_with_its_origin(self):
        before = snapshot(self.env.notes)
        error, r = await self.call("lore_remember", MEMORY)
        self.assertFalse(error, r)
        self.assertEqual("created", r["status"])
        self.assertEqual("unverified", r["verification"])
        self.assertEqual("pending", r["indexing"]["state"])  # no lore HTTP server in this test
        [file] = self.files()
        self.assertRegex(file.name, NAME)
        self.assertEqual(f"memories:{file.name}", r["source"])
        self.assertEqual(0o600, stat.S_IMODE(file.stat().st_mode))
        text = file.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(text.split("---\n")[1])
        self.assertEqual("unverified", frontmatter["status"])
        self.assertEqual("claude-code 2.1.231", frontmatter["author"])
        self.assertEqual("fact", frontmatter["kind"])
        self.assertEqual(["DEC-034", "ADR-0007"], frontmatter["decisions"])
        self.assertNotIn("category", frontmatter)  # the category comes from the taxonomy, not from the client
        self.assertIn("# Order queue: dispatcher assigns", text)
        self.assertIn("**Unverified:**", text)
        self.assertIn("- commit `06e4b4d`", text)
        self.assertEqual(before, snapshot(self.env.notes))  # no existing .md changed
        self.assertEqual([], [p.name for p in self.env.memories.iterdir() if p.name.startswith(".writing")])
        [entry] = self.memory_audit()
        self.assertEqual("mcp", entry["origin"])
        self.assertEqual("claude-code 2.1.231", entry["agent"])
        self.assertEqual(
            {"source": "memories", "file": file.name, "status": "created", "sha256": frontmatter["sha256"][:12], "indexing": "pending"},
            entry["details"]["memory"],
        )

    async def test_same_content_is_neither_duplicated_nor_overwritten(self):
        _, first = await self.call("lore_remember", MEMORY)
        [file] = self.files()
        content, mtime = file.read_bytes(), file.stat().st_mtime_ns
        error, second = await self.call("lore_remember", {**MEMORY, "title": "Another title, same text"})
        self.assertFalse(error)
        self.assertEqual("already_existed", second["status"])
        self.assertEqual(first["source"], second["source"])
        self.assertEqual([file], self.files())
        self.assertEqual((content, mtime), (file.read_bytes(), file.stat().st_mtime_ns))

    async def test_the_name_is_generated_by_the_server(self):
        for title in ("../../etc/passwd", "/tmp/x.md", "Database credentials live in the vault", "   ###   "):
            with self.subTest(title=title):
                error, r = await self.call(
                    "lore_remember", {**MEMORY, "title": title, "content": f"{MEMORY['content']} Variant: {title}."}
                )
                if title.strip("# ") == "":
                    self.assertTrue(error)  # title without text
                    continue
                self.assertFalse(error, r)
                name = r["source"].split(":", 1)[1]
                self.assertRegex(name, NAME)
                self.assertTrue((self.env.memories / name).is_file())
                self.assertNotIn("credential", name)  # the deny list would refuse the file at indexing time
        self.assertEqual(3, len(self.files()))
        self.assertTrue(all(p.parent == self.env.memories for p in self.files()))

    # ------------------------------------------------------------------ refusals

    async def test_secret_is_refused_and_stays_out_of_the_audit_log(self):
        for field, value in (
            ("content", "To get in use SHOP_JWT_SECRET=abcdefghijklmnopqrstuvwxyz0123 on the production server."),
            ("content", "The GitHub token is ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD and lives in the vault."),
            ("evidence", ["postgres://admin:StrongPass123@10.0.0.5/shop"]),
        ):
            with self.subTest(field=field):
                error, text = await self.call("lore_remember", {**MEMORY, field: value})
                self.assertTrue(error)
                self.assertIn("secret", str(text))
        self.assertEqual([], self.files())
        entries = self.memory_audit()
        self.assertEqual(3, len(entries))
        raw = json.dumps(entries)
        for secret in ("abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijklmnopqrstuvwxyz", "StrongPass123"):
            self.assertNotIn(secret, raw)
        self.assertTrue(all(r["details"]["result"] == "error" for r in entries))

    async def test_invalid_input_is_refused(self):
        cases = {
            "kind": {"kind": "order"},
            "short": {"content": "short"},
            "long": {"content": "x" * 5000},
            "title": {"title": "ab"},
            "decision": {"related_decisions": ["DEC-1; rm -rf /"]},
            "evidence": {"evidence": [f"source {i}" for i in range(11)]},
        }
        for name, extra in cases.items():
            with self.subTest(case=name):
                error, _ = await self.call("lore_remember", {**MEMORY, **extra})
                self.assertTrue(error)
        self.assertEqual([], self.files())

    async def test_hourly_limit(self):
        layer = McpLayer(self.env.settings(memories=True, mcp_max_memories_per_hour=2))
        try:
            for i in range(2):
                error, _ = await self.call("lore_remember", {**MEMORY, "content": f"{MEMORY['content']} Number {i}."}, layer)
                self.assertFalse(error)
            error, text = await self.call("lore_remember", {**MEMORY, "content": f"{MEMORY['content']} Number 3."}, layer)
            self.assertTrue(error)
            self.assertIn("Limit of 2 memories per hour", str(text))
            self.assertEqual(2, len(self.files()))
            error, repeated = await self.call("lore_remember", {**MEMORY, "content": f"{MEMORY['content']} Number 0."}, layer)
            self.assertFalse(error)  # repeating does not count: nothing new is created
            self.assertEqual("already_existed", repeated["status"])
        finally:
            layer.close()

    async def test_disabled_does_not_show_up_and_the_server_is_read_only(self):
        layer = McpLayer(self.env.settings(memories=True, mcp_allow_memory=False, mcp_allow_forget=False))
        try:
            async with Client(create_server(layer), client_info=CLIENT) as client:
                names = {t.name for t in (await client.list_tools()).tools}
                status = json.loads((await client.call_tool("lore_status", {})).content[0].text)
            self.assertNotIn("lore_remember", names)
            self.assertNotIn("lore_forget", names)
            self.assertTrue(status["mcp"]["read_only"])
            self.assertFalse(status["mcp"]["memory"]["can_create"])
            with self.assertRaises(Exception):
                layer.remember(None, {}, "t", "c", None, None, None, None)  # not even from the inside
        finally:
            layer.close()
        self.assertEqual([], self.files())

    async def test_without_a_source_for_the_folder_nothing_is_written(self):
        layer = McpLayer(self.env.settings())  # sources.yaml without the memories source
        try:
            error, text = await self.call("lore_remember", MEMORY, layer)
        finally:
            layer.close()
        self.assertTrue(error)
        self.assertIn("would never be indexed", str(text))
        self.assertEqual([], self.files())

    # ------------------------------------------------------------------ after saving

    async def test_indexed_it_becomes_an_explicit_searchable_memory(self):
        await self.call("lore_remember", MEMORY)
        self.index()
        error, found = await self.call("lore_search", {"query": "dispatcher queue technician"})
        self.assertFalse(error)
        hit = next(i for i in found["results"] if i["source"].startswith("memories:"))
        with closing(connect(self.env.db)) as conn:
            doc = conn.execute("SELECT category, category_confidence FROM document WHERE id = ?", (hit["document_id"],)).fetchone()
            mentions = {m["key"] for m in conn.execute("SELECT key FROM mention WHERE document_id = ?", (hit["document_id"],))}
        self.assertEqual(("Memories/MCP", "explicit"), (doc["category"], doc["category_confidence"]))
        self.assertIn("DEC-034", mentions)  # the relation with the decision has written evidence
        _, context = await self.call("lore_get_context", {"task": "Dispatcher queue technician"})
        memory = next(m for m in context["memories"] if m["source"].startswith("memories:"))
        self.assertIs(False, memory["verified"])
        self.assertTrue(any("nobody has verified" in w for w in context["warnings"]))
        error, repeated = await self.call("lore_remember", MEMORY)
        self.assertEqual("indexed", repeated["indexing"]["state"])
        self.assertEqual(hit["document_id"], repeated["indexing"]["document_id"])

    async def test_asks_the_local_server_to_index(self):
        app = create_app(self.cfg, sync=False)
        with Server(app) as server:
            layer = McpLayer(self.env.settings(memories=True, http_port=server.port, mcp_memory_reindex=True))
            try:
                error, r = await self.call("lore_remember", MEMORY, layer)
            finally:
                layer.close()
        self.assertFalse(error, r)
        self.assertEqual("indexed", r["indexing"]["state"])
        self.assertIsInstance(r["indexing"]["document_id"], int)
        with closing(connect(self.env.db)) as conn:
            request = next(a for a in audit.list_entries(conn, 50) if a["action"] == "reindex_requested")
        self.assertEqual("api", request["origin"])
        self.assertIn("lore-mcp", request["agent"])
        self.assertEqual("indexed", self.memory_audit()[0]["details"]["memory"]["indexing"])

    async def test_server_down_stays_pending(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]  # closed when leaving the block
        layer = McpLayer(self.env.settings(memories=True, http_port=port, mcp_memory_reindex=True))
        try:
            error, r = await self.call("lore_remember", MEMORY, layer)
        finally:
            layer.close()
        self.assertFalse(error)
        self.assertEqual("created", r["status"])
        self.assertEqual("pending", r["indexing"]["state"])
        self.assertIn("not running", r["indexing"]["note"])

    async def test_panel_lists_created_memories(self):
        await self.call("lore_remember", MEMORY)
        self.index()
        lore = Lore(self.cfg)
        with closing(connect(self.env.db)) as conn:
            panel = lore.mcp_panel(conn, [], "lore-mcp")
        self.assertFalse(panel["read_only"])
        self.assertEqual("memories", panel["memories"]["source"])
        self.assertEqual(1, panel["memories"]["created"])
        [recent] = panel["memories"]["recent"]
        self.assertEqual("Order queue: dispatcher assigns", recent["title"])
        self.assertEqual("claude-code 2.1.231", recent["client"])
        self.assertEqual("created", recent["status"])
        self.assertIsInstance(recent["document_id"], int)

    def test_database_stays_closed_to_the_mcp_server(self):
        # The memory enters the index through lore; the MCP server still cannot write documents/chunks.
        from lore.mcp_server import open_read_only

        conn = open_read_only(self.env.db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("INSERT INTO document (source_id, path) VALUES ('memories', 'x.md')")
        finally:
            conn.close()


class TestForget(McpMemoryBase):
    """lore_forget: only memories created through MCP, only into the trash, with a reason and an audit entry."""

    def setUp(self):
        self.env = Env()
        write(self.env.notes, "01-CURRENT-STATE.md", "# State\n\nNothing was used by a real operator yet.\n")
        self.cfg = self.env.settings(memories=True)
        self.layer = McpLayer(self.cfg)
        self.index()

    def tearDown(self):
        self.layer.close()
        self.env.close()

    def trash(self) -> list[Path]:
        folder = self.env.memories / TRASH
        return sorted(folder.glob("*.md")) if folder.exists() else []

    async def remember(self, **extra) -> dict:
        error, r = await self.call("lore_remember", {**MEMORY, **extra})
        self.assertFalse(error, r)
        self.index()
        return r

    async def found_in_search(self) -> bool:
        _, r = await self.call("lore_search", {"query": "dispatcher queue technician"})
        return any(i["source"].startswith("memories:") for i in r["results"])

    async def test_forget_moves_to_the_trash_and_leaves_the_search(self):
        r = await self.remember()
        [file] = self.files()
        content = file.read_bytes()
        self.assertTrue(await self.found_in_search())

        error, f = await self.call("lore_forget", {"memory": r["memory_id"], "reason": "Contradicted by the product owner"})
        self.assertFalse(error, f)
        self.assertEqual("forgotten", f["status"])
        self.assertTrue(f["recoverable"])
        self.assertEqual("Order queue: dispatcher assigns", f["title"])
        self.assertEqual([], self.files())
        [trashed] = self.trash()
        self.assertEqual(content, trashed.read_bytes())  # nothing was deleted or changed
        self.assertEqual(f"{TRASH}/{trashed.name}", f["trash"])
        self.assertEqual("pending", f["indexing"]["state"])  # no lore HTTP server in this test

        self.index()
        self.assertFalse(await self.found_in_search())
        # moving the file back restores the memory
        trashed.rename(file)
        self.index()
        self.assertTrue(await self.found_in_search())

    async def test_accepts_source_and_document_id_and_audits(self):
        r = await self.remember()
        error, f = await self.call("lore_forget", {"memory": r["source"], "reason": "duplicate of another memory"})
        self.assertFalse(error, f)
        with closing(connect(self.env.db)) as conn:
            [entry] = [a for a in audit.list_entries(conn, 100) if a["action"] == "mcp_memory_forgotten"]
        self.assertEqual("claude-code 2.1.231", entry["agent"])
        self.assertEqual("forgotten", entry["details"]["memory"]["status"])
        self.assertEqual("duplicate of another memory", entry["details"]["memory"]["reason"])
        self.assertEqual("ok", entry["details"]["result"])

        other = await self.remember(content=f"{MEMORY['content']} Second version.")
        with closing(connect(self.env.db)) as conn:
            doc = conn.execute("SELECT id FROM document WHERE source_id = 'memories' AND status != 'removed'").fetchone()[0]
        error, f = await self.call("lore_forget", {"memory": f"doc:{doc}", "reason": "by the document id"})
        self.assertFalse(error, f)
        self.assertEqual(other["memory_id"], f["memory_id"])

    async def test_does_not_touch_other_sources_nor_paths(self):
        with closing(connect(self.env.db)) as conn:
            doc_notes = conn.execute("SELECT id FROM document WHERE source_id = 'notes' LIMIT 1").fetchone()[0]
        notes_before = snapshot(self.env.notes)
        cases = [
            str(doc_notes), f"doc:{doc_notes}", "notes:01-CURRENT-STATE.md", "01-CURRENT-STATE.md",
            "../../etc/passwd", "../01-CURRENT-STATE", "/etc/passwd", "memories:../x.md", "anything",
        ]
        for case in cases:
            with self.subTest(memory=case):
                error, text = await self.call("lore_forget", {"memory": case, "reason": "test attempt"})
                self.assertTrue(error)
                self.assertNotIn("root:", str(text))
        self.assertEqual(notes_before, snapshot(self.env.notes))
        self.assertEqual([], self.trash())

    async def test_refuses_symbolic_link_and_missing_memory(self):
        target = self.env.notes / "01-CURRENT-STATE.md"
        self.env.memories.mkdir(parents=True, exist_ok=True)
        name = "2026-10-02-120000-fake-aaaaaaaaaaaa.md"
        (self.env.memories / name).symlink_to(target)
        error, _ = await self.call("lore_forget", {"memory": name, "reason": "malicious symbolic link"})
        self.assertTrue(error)
        self.assertTrue(target.exists() and (self.env.memories / name).is_symlink())
        error, _ = await self.call(
            "lore_forget", {"memory": "2026-10-02-130000-does-not-exist-bbbbbbbbbbbb", "reason": "it does not exist"}
        )
        self.assertTrue(error)
        self.assertEqual([], self.trash())

    async def test_requires_a_reason_without_secrets(self):
        r = await self.remember()
        for reason in ("", "ok", "   ", "leaked SHOP_JWT_SECRET=abcdefghijklmnopqrstuvwxyz0123 here"):
            with self.subTest(reason=reason):
                error, _ = await self.call("lore_forget", {"memory": r["memory_id"], "reason": reason})
                self.assertTrue(error)
        self.assertEqual(1, len(self.files()))
        with closing(connect(self.env.db)) as conn:
            raw = json.dumps(audit.list_entries(conn, 100))
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", raw)

    async def test_hourly_limit(self):
        layer = McpLayer(self.env.settings(memories=True, mcp_max_memories_per_hour=1))
        try:
            a = (await self.call("lore_remember", {**MEMORY, "content": f"{MEMORY['content']} Variant A."}, layer))[1]
            error, _ = await self.call("lore_forget", {"memory": a["memory_id"], "reason": "first forget"}, layer)
            self.assertFalse(error)
            self.assertEqual(1, len(self.trash()))
            self.index()
            b = await self.call("lore_remember", {**MEMORY, "content": f"{MEMORY['content']} Variant B."}, layer)
            self.assertTrue(b[0])  # creating is also capped (1/h)
        finally:
            layer.close()

    async def test_disabled_does_not_show_up(self):
        layer = McpLayer(self.env.settings(memories=True, mcp_allow_forget=False))
        try:
            async with Client(create_server(layer), client_info=CLIENT) as client:
                names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn("lore_remember", names)
            self.assertNotIn("lore_forget", names)
            with self.assertRaises(Exception):
                layer.forget(None, {}, "x", "any reason")  # not even from the inside
        finally:
            layer.close()

    async def test_panel_lists_forgotten_memories(self):
        r = await self.remember()
        await self.call("lore_forget", {"memory": r["memory_id"], "reason": "outdated information"})
        self.index()
        with closing(connect(self.env.db)) as conn:
            panel = Lore(self.cfg).mcp_panel(conn, [], "lore-mcp")
        self.assertEqual(1, panel["memories"]["forgotten"])
        self.assertTrue(panel["memories"]["forget_allowed"])
        forgotten = next(m for m in panel["memories"]["recent"] if m["status"] == "forgotten")
        self.assertEqual("Order queue: dispatcher assigns", forgotten["title"])
        self.assertEqual("outdated information", forgotten["reason"])


class TestStdioProcessMemory(unittest.IsolatedAsyncioTestCase):
    async def test_real_process_writes_only_to_the_configured_folder(self):
        env = Env()
        try:
            write(env.notes, "note.md", "# Note\n\nText.\n")
            cfg = env.settings(memories=True)
            Lore(cfg).indexer.run(RunContext(origin="test"))
            process_env = {**os.environ, **env.process_env(cfg)}
            params = StdioServerParameters(command=sys.executable, args=["-m", "lore.mcp_server"], env=process_env)
            async with Client(params, client_info=CLIENT) as client:
                r = await client.call_tool("lore_remember", MEMORY)
            self.assertFalse(r.is_error, r.content[0].text)
            self.assertEqual(1, len(list(env.memories.glob("*.md"))))
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
