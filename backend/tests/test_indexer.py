"""Incremental indexing: change detection by hash, removal without deleting, errors, audit."""

import hashlib
import os
import sqlite3
import time
import unittest
from contextlib import closing

from support import Env, create_repository, git, write

from lore import audit
from lore.db import connect, json_load
from lore.indexer import PROCESSOR_VERSION, IndexingInProgress, RunContext, _lock
from lore.sources import Source

CONTEXT = RunContext(origin="test")


def snapshot(root):
    """hash + mtime + mode of every file: proof that nothing was changed."""
    state = {}
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            info = os.stat(path)
            with open(path, "rb") as f:
                state[path] = (hashlib.sha256(f.read()).hexdigest(), info.st_mtime_ns, info.st_mode)
    return state


class IndexerBase(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        write(self.env.notes, "00-INDEX.md", "# Index\n\nSee [[02-ARCHITECTURE]] and [[decisions/DEC-001-Scope]].\n")
        write(self.env.notes, "02-ARCHITECTURE.md", "# Architecture\n\nFastAPI and MySQL.\n\n## Stack\nPython 3.12.\n")
        write(self.env.notes, "decisions/DEC-001-Scope.md", "# DEC-001 — Scope\n\n- **Date:** 2026-08-01\n\nComplement.\n")
        self.indexer = self.env.indexer()

    def tearDown(self):
        self.env.close()

    def db(self) -> closing[sqlite3.Connection]:
        return closing(connect(self.env.db))

    def doc(self, path):
        with self.db() as conn:
            return conn.execute("SELECT * FROM document WHERE path = ?", (path,)).fetchone()


class TestIndexing(IndexerBase):
    def test_first_run_records_everything(self):
        r = self.indexer.run(CONTEXT)
        self.assertEqual("completed", r.status)
        self.assertEqual(3, r.totals["new"])
        self.assertEqual(3, r.totals["documents"])
        d = self.doc("decisions/DEC-001-Scope.md")
        self.assertEqual("indexed", d["status"])
        self.assertEqual("DEC-001 — Scope", d["title"])
        self.assertEqual("Decisions", d["category"])
        self.assertEqual({"Date": "2026-08-01"}, json_load(d["metadata"]))
        self.assertEqual(1, d["version"])
        self.assertEqual(self.indexer.processor_version, d["processor_version"])
        self.assertTrue(d["processor_version"].startswith(PROCESSOR_VERSION + "+"))
        raw = (self.env.notes / "decisions/DEC-001-Scope.md").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), d["hash"])
        self.assertEqual(len(raw), d["size"])
        with self.db() as conn:
            chunks = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
            fts = conn.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0]
            self.assertEqual(chunks, fts)
            self.assertGreater(chunks, 0)

    def test_nothing_changed_nothing_is_reprocessed(self):
        self.indexer.run(CONTEXT)
        before = self.doc("02-ARCHITECTURE.md")["indexed_at"]
        time.sleep(1.1)
        r = self.indexer.run(CONTEXT)
        self.assertEqual(3, r.totals["unchanged"])
        self.assertEqual(0, r.totals["new"] + r.totals["changed"] + r.totals["reprocessed"])
        self.assertEqual(before, self.doc("02-ARCHITECTURE.md")["indexed_at"])

    def test_only_the_changed_file_is_reprocessed_and_gets_a_version(self):
        self.indexer.run(CONTEXT)
        untouched = self.doc("00-INDEX.md")["indexed_at"]
        time.sleep(1.1)
        write(self.env.notes, "02-ARCHITECTURE.md", "# Architecture\n\nNow with PostgreSQL.\n")
        r = self.indexer.run(CONTEXT)
        self.assertEqual(1, r.totals["changed"])
        self.assertEqual(2, r.totals["unchanged"])
        d = self.doc("02-ARCHITECTURE.md")
        self.assertEqual(2, d["version"])
        self.assertIn("PostgreSQL", d["content"])
        self.assertEqual(untouched, self.doc("00-INDEX.md")["indexed_at"])
        with self.db() as conn:
            events = [v["event"] for v in conn.execute(
                "SELECT event FROM document_version WHERE document_id = ? ORDER BY id", (d["id"],))]
            self.assertEqual(["new", "changed"], events)
            items = conn.execute("SELECT path, event FROM run_item WHERE run_id = ?", (r.run_id,)).fetchall()
            self.assertEqual([("02-ARCHITECTURE.md", "changed")], [tuple(i) for i in items])

    def test_changing_only_the_file_date_does_not_reprocess(self):
        self.indexer.run(CONTEXT)
        os.utime(self.env.notes / "00-INDEX.md", (1, 1))
        r = self.indexer.run(CONTEXT)
        self.assertEqual(0, r.totals["changed"])

    def test_full_reprocesses_without_creating_a_version(self):
        self.indexer.run(CONTEXT)
        r = self.indexer.run(CONTEXT, full=True)
        self.assertEqual(3, r.totals["reprocessed"])
        self.assertEqual(1, self.doc("00-INDEX.md")["version"])

    def test_changing_the_decision_rules_reprocesses(self):
        from dataclasses import replace

        from lore.sources import DecisionRules

        self.indexer.run(CONTEXT)
        other = self.env.indexer(replace(self.env.sources(), decisions=DecisionRules(patterns=(r"RFC-\d{2}",))))
        self.assertNotEqual(self.indexer.processor_version, other.processor_version)
        r = other.run(CONTEXT)
        self.assertEqual(3, r.totals["reprocessed"])
        with self.db() as conn:  # mentions follow the new rules: DEC-001 is no longer a decision code
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM mention WHERE type = 'decision'").fetchone()[0])

    def test_deleted_file_becomes_removed_without_deleting_history(self):
        self.indexer.run(CONTEXT)
        (self.env.notes / "02-ARCHITECTURE.md").unlink()
        r = self.indexer.run(CONTEXT)
        self.assertEqual(1, r.totals["removed"])
        d = self.doc("02-ARCHITECTURE.md")
        self.assertEqual("removed", d["status"])
        self.assertIsNotNone(d["removed_at"])
        with self.db() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM chunk WHERE document_id = ?", (d["id"],)).fetchone()[0])
            events = [v[0] for v in conn.execute("SELECT event FROM document_version WHERE document_id = ?", (d["id"],))]
            self.assertEqual(["new", "removed"], events)
        # back again → restored, same row
        write(self.env.notes, "02-ARCHITECTURE.md", "# Architecture\n\nBack.\n")
        r = self.indexer.run(CONTEXT)
        self.assertEqual(1, r.totals["restored"])
        self.assertEqual(d["id"], self.doc("02-ARCHITECTURE.md")["id"])
        self.assertEqual("indexed", self.doc("02-ARCHITECTURE.md")["status"])

    def test_unavailable_source_removes_nothing(self):
        self.indexer.run(CONTEXT)
        hidden = self.env.base / "notes-unmounted"
        self.env.notes.rename(hidden)
        try:
            r = self.indexer.run(CONTEXT)
        finally:
            hidden.rename(self.env.notes)
        self.assertEqual(["notes"], r.unavailable_sources)
        self.assertEqual(0, r.totals["removed"])
        self.assertEqual("indexed", self.doc("00-INDEX.md")["status"])

    def test_processing_error_does_not_bring_the_others_down_and_is_not_retried(self):
        from unittest import mock

        import lore.indexer as module

        original = module.parse

        def fail_on_one(data, path):
            if path == "02-ARCHITECTURE.md":
                raise ValueError("parser broke")
            return original(data, path)

        with mock.patch.object(module, "parse", side_effect=fail_on_one):
            r = self.indexer.run(CONTEXT)
            self.assertEqual(1, r.totals["errors"])
            self.assertEqual(2, r.totals["new"])
            d = self.doc("02-ARCHITECTURE.md")
            self.assertEqual("error", d["status"])
            self.assertEqual("processing", d["error_kind"])
            self.assertIn("parser broke", d["error"])
            # the same failure with the same content is not retried on every cycle
            r2 = self.indexer.run(RunContext(origin="scheduled"))
            self.assertFalse(r2.recorded)
        # content changes → retried and now it works
        write(self.env.notes, "02-ARCHITECTURE.md", "# Architecture\n\nFixed.\n")
        self.indexer.run(CONTEXT)
        self.assertEqual("indexed", self.doc("02-ARCHITECTURE.md")["status"])

    def test_unreadable_file_keeps_the_last_version_and_returns_when_readable(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any file")
        self.indexer.run(CONTEXT)
        file = self.env.notes / "02-ARCHITECTURE.md"
        os.chmod(file, 0)
        try:
            r = self.indexer.run(CONTEXT)
        finally:
            os.chmod(file, 0o644)
        self.assertEqual(1, r.totals["errors"])
        d = self.doc("02-ARCHITECTURE.md")
        self.assertEqual(("error", "read"), (d["status"], d["error_kind"]))
        with self.db() as conn:  # the last good version stays searchable
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM chunk WHERE document_id = ?", (d["id"],)).fetchone()[0], 0)
        r = self.indexer.run(CONTEXT)
        self.assertEqual(1, r.totals["reprocessed"])
        self.assertEqual("indexed", self.doc("02-ARCHITECTURE.md")["status"])

    def test_links_are_resolved_and_updated_when_the_target_appears(self):
        write(self.env.notes, "modules/HR.md", "# HR\n\n[[../decisions/DEC-002]] and [[00-INDEX]] and [[Missing]].\n")
        self.indexer.run(CONTEXT)
        with self.db() as conn:
            links = {r["target"]: r["target_document_id"] for r in conn.execute("SELECT target, target_document_id FROM link")}
        self.assertIsNotNone(links["00-INDEX"])
        self.assertIsNotNone(links["02-ARCHITECTURE"])
        self.assertIsNotNone(links["decisions/DEC-001-Scope"])
        self.assertIsNone(links["../decisions/DEC-002"])
        self.assertIsNone(links["Missing"])
        write(self.env.notes, "decisions/DEC-002.md", "# DEC-002\n")
        self.indexer.run(CONTEXT)
        with self.db() as conn:
            target = conn.execute("SELECT target_document_id FROM link WHERE target = '../decisions/DEC-002'").fetchone()[0]
            self.assertEqual(self.doc("decisions/DEC-002.md")["id"], target)

    def test_reclassifies_without_reprocessing_when_the_taxonomy_changes(self):
        self.indexer.run(CONTEXT)
        from dataclasses import replace

        tax = self.indexer.taxonomy
        without_decisions = tuple(r for r in tax.rules if r.category != "Decisions")  # drops the decisions/ rule
        self.indexer.taxonomy = replace(tax, rules=without_decisions)
        r = self.indexer.run(CONTEXT)
        self.assertEqual(1, r.totals["reclassified"])
        self.assertEqual(0, r.totals["reprocessed"])
        d = self.doc("decisions/DEC-001-Scope.md")
        self.assertEqual(("Uncategorized", "suggestion"), (d["category"], d["category_confidence"]))


class TestReadOnlyAndAudit(IndexerBase):
    def test_sources_stay_intact(self):
        self.indexer.run(CONTEXT)
        write(self.env.notes, "new.md", "# New\n")
        before = snapshot(self.env.notes)
        self.indexer.run(CONTEXT, full=True)
        with self.db() as conn:
            self.indexer.pending(conn)
        self.assertEqual(before, snapshot(self.env.notes))

    def test_database_lives_outside_the_sources_and_only_for_the_owner(self):
        self.indexer.run(CONTEXT)
        self.assertFalse(self.env.db.is_relative_to(self.env.notes))
        self.assertEqual(0o600, self.env.db.stat().st_mode & 0o777)

    def test_run_goes_to_the_audit_log_and_the_log_refuses_changes(self):
        r = self.indexer.run(RunContext(origin="api", ip="127.0.0.1", agent="test"))
        with self.db() as conn:
            entries = audit.list_entries(conn)
            self.assertEqual(["reindex_completed", "reindex_requested"], [a["action"] for a in entries])
            self.assertEqual(r.run_id, entries[0]["details"]["run_id"])
            self.assertEqual("127.0.0.1", entries[0]["ip"])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE audit SET action = 'x'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM audit")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM document_version")

    def test_scheduled_check_without_changes_does_not_pollute_history(self):
        self.indexer.run(CONTEXT)
        r = self.indexer.run(RunContext(origin="scheduled"))
        self.assertFalse(r.recorded)
        with self.db() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM run").fetchone()[0])
            self.assertIsNotNone(conn.execute("SELECT value FROM meta WHERE key = 'last_check'").fetchone())

    def test_pending_shows_what_changed_without_indexing(self):
        self.indexer.run(CONTEXT)
        write(self.env.notes, "02-ARCHITECTURE.md", "# Changed\n")
        write(self.env.notes, "new.md", "# New\n")
        (self.env.notes / "00-INDEX.md").unlink()
        with self.db() as conn:
            pending = {(p["path"], p["type"]) for p in self.indexer.pending(conn)}
        self.assertEqual({("02-ARCHITECTURE.md", "changed"), ("new.md", "new"), ("00-INDEX.md", "removed")}, pending)
        self.assertEqual("indexed", self.doc("00-INDEX.md")["status"])  # nothing was applied

    def test_two_runs_at_the_same_time_are_refused(self):
        lock = _lock(self.env.db)
        lock.acquire()
        try:
            with self.assertRaises(IndexingInProgress):
                self.indexer.run(CONTEXT)
        finally:
            lock.release()


class TestGitSource(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.repo = self.env.base / "acme-api"
        create_repository(self.repo, {"README.md": "# Acme API\n", "docs/CHECKOUT.md": "# Checkout\n\nSecurity.\n"})
        self.source = Source("acme-api", "Acme API", "git", self.repo, ("*.md", "docs/**/*.md"), ref="main")
        self.indexer = self.env.indexer(self.env.sources(self.source))

    def tearDown(self):
        self.env.close()

    def test_new_commit_on_the_ref_reprocesses_only_the_changed_blob(self):
        r = self.indexer.run(CONTEXT)
        self.assertEqual(2, r.totals["new"])
        write(self.repo, "docs/CHECKOUT.md", "# Checkout\n\nChanged.\n")
        git(self.repo, "commit", "-qam", "change checkout")
        r = self.indexer.run(CONTEXT)
        self.assertEqual(1, r.totals["changed"])
        self.assertEqual(1, r.totals["unchanged"])
        with closing(connect(self.env.db)) as conn:
            ref = conn.execute("SELECT ref_commit FROM source WHERE id = 'acme-api'").fetchone()[0]
            self.assertEqual(git(self.repo, "rev-parse", "HEAD"), ref)


if __name__ == "__main__":
    unittest.main()
