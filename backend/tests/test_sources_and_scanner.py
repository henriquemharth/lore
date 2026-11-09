"""Discovery of .md files: patterns, folder and git sources, limits and read-only behaviour."""

import os
import unittest
from pathlib import Path

from support import Env, create_repository, git, write

from lore.scanner import load, scan_folder, scan_git, sha256
from lore.sources import ConfigError, Source, load_sources, match


class TestPatterns(unittest.TestCase):
    def test_star_does_not_cross_folders_and_double_star_does(self):
        self.assertTrue(match("a.md", "*.md"))
        self.assertFalse(match("x/a.md", "*.md"))
        self.assertTrue(match("a.md", "**/*.md"))
        self.assertTrue(match("x/y/a.md", "**/*.md"))
        self.assertTrue(match("docs/x/y.md", "docs/**/*.md"))
        self.assertTrue(match("docs/y.md", "docs/**/*.md"))
        self.assertFalse(match("other/y.md", "docs/**/*.md"))

    def test_wildcard_in_the_middle_and_case_insensitive(self):
        self.assertTrue(match("integrations/SECURITY-AUDIT-2026.md", "integrations/*AUDIT*.md"))
        self.assertTrue(match("Decisions/dec-1.md", "decisions/*.md"))
        self.assertTrue(match("a1.md", "a[0-9].md"))
        self.assertFalse(match("ab.md", "a[!b].md"))

    def test_source_only_accepts_visible_md(self):
        source = Source("f", "F", "folder", FAKE_ROOT, ("**/*",), ("drafts/*",))
        self.assertTrue(source.accepts("note.md"))
        self.assertFalse(source.accepts("note.txt"))
        self.assertFalse(source.accepts(".obsidian/app.md"))
        self.assertFalse(source.accepts("folder/.hidden.md"))
        self.assertFalse(source.accepts("drafts/x.md"))


FAKE_ROOT = Path("/does/not/matter")  # the pattern tests never touch the disk


class TestSourcesConfig(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def _yaml(self, text: str, folder: Path | None = None):
        file = (folder or self.env.base) / "sources.yaml"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return file

    def test_refuses_duplicate_id_invalid_type_and_dangerous_ref(self):
        cases = [
            "sources:\n  - {id: a1, type: folder, root: /x}\n  - {id: a1, type: folder, root: /y}\n",
            "sources:\n  - {id: a1, type: ftp, root: /x}\n",
            "sources:\n  - {id: a1, type: git, root: /x, ref: '--upload-pack=x'}\n",
            "sources:\n  - {id: A!, type: folder, root: /x}\n",
            "sources: []\n",
            "sources:\n  - {id: a1, type: folder, root: /x}\ndecisions: {patterns: ['DEC-(']}\n",
            "sources:\n  - {id: a1, type: folder, root: /x}\ndecisions: {patterns: ['x*']}\n",
            "sources:\n  - {id: a1, type: folder, root: /x}\ndomains: {'Not Valid': {description: x}}\n",
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ConfigError):
                load_sources(self._yaml(text))

    def test_relative_root_resolves_from_the_folder_of_the_file(self):
        project = self.env.base / "project"
        file = self._yaml(
            "sources:\n"
            "  - {id: docs, type: folder, root: ./docs}\n"
            "  - {id: here, type: git, root: ., ref: HEAD}\n"
            "  - {id: up, type: folder, root: ../shared}\n"
            "  - {id: absolute, type: folder, root: /tmp/abs}\n"
            "  - {id: home, type: folder, root: ~/notes}\n",
            folder=project,
        )
        roots = {s.id: s.root for s in load_sources(file).sources}
        self.assertEqual(project.resolve() / "docs", roots["docs"])
        self.assertEqual(project.resolve(), roots["here"])
        self.assertEqual(self.env.base.resolve() / "shared", roots["up"])
        self.assertEqual(Path("/tmp/abs"), roots["absolute"])
        self.assertEqual(Path.home() / "notes", roots["home"])

    def test_domains_and_decision_patterns(self):
        config = load_sources(self._yaml(
            "sources:\n  - {id: a1, type: folder, root: /x}\n  - {id: b1, type: folder, root: /y, domain: billing}\n"
            "domains:\n  billing: {description: of the Acme billing service}\n"
            "decisions:\n  patterns: ['RFC-\\d{2}']\n  numbered_folders: [records]\n"
        ))
        self.assertEqual(("default", "billing"), config.domains())
        self.assertEqual("of the Acme billing service", config.describe("billing"))
        self.assertIsNone(config.describe("default"))
        self.assertEqual("RFC-07", config.decisions.code_from_path("rfc/RFC-07-cache.md"))
        self.assertEqual("ADR-0003", config.decisions.code_from_path("records/0003-x.md"))
        self.assertIsNone(config.decisions.code_from_path("decisions/DEC-001.md"))


class TestFolderScan(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        write(self.env.notes, "00-INDEX.md", "# Index\n")
        write(self.env.notes, "decisions/DEC-001.md", "# DEC-001\n")
        write(self.env.notes, ".obsidian/workspace.md", "hidden")
        write(self.env.notes, "notes.txt", "not markdown")
        write(self.env.notes, ".trash/old.md", "trash")

    def tearDown(self):
        self.env.close()

    def test_finds_md_and_records_path_name_size_hash_date(self):
        s = scan_folder(self.env.notes_source(), 100_000)
        self.assertTrue(s.available)
        self.assertEqual(["00-INDEX.md", "decisions/DEC-001.md"], [f.path for f in s.files])
        file = s.files[1]
        raw = (self.env.notes / "decisions/DEC-001.md").read_bytes()
        self.assertEqual("DEC-001.md", file.name)
        self.assertEqual(len(raw), file.size)
        self.assertEqual(sha256(raw), file.hash)
        self.assertEqual(file.hash, file.identity)
        self.assertRegex(file.modified_at or "", r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")

    def test_missing_folder_is_unavailable_without_error(self):
        source = Source("x", "X", "folder", self.env.base / "missing", ("**/*.md",))
        s = scan_folder(source, 100_000)
        self.assertFalse(s.available)
        self.assertEqual([], s.files)

    def test_file_too_large_becomes_an_error_and_is_not_read(self):
        write(self.env.notes, "large.md", "x" * 500)
        s = scan_folder(self.env.notes_source(), 100)
        self.assertIn("large.md", [p for p, _ in s.errors])
        self.assertNotIn("large.md", [f.path for f in s.files])

    def test_symbolic_link_outside_the_source_is_ignored(self):
        outside = write(self.env.base, "outside/secret.md", "# outside")
        os.symlink(outside, self.env.notes / "shortcut.md")
        s = scan_folder(self.env.notes_source(), 100_000)
        self.assertNotIn("shortcut.md", [f.path for f in s.files])
        self.assertTrue(any("shortcut.md" in w for w in s.warnings))

    def test_unreadable_file_becomes_an_error(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any file")
        blocked = write(self.env.notes, "blocked.md", "# x")
        os.chmod(blocked, 0)
        try:
            s = scan_folder(self.env.notes_source(), 100_000)
        finally:
            os.chmod(blocked, 0o644)
        self.assertIn("blocked.md", [p for p, _ in s.errors])


class TestGitScan(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.repo = self.env.base / "repo"
        self.commit = create_repository(
            self.repo,
            {"README.md": "# Read me\n", "docs/a.md": "# A\n", "code.py": "print(1)\n", ".agents/s.md": "# s\n"},
        )
        self.source = Source("acme-api", "Acme API", "git", self.repo, ("*.md", "docs/**/*.md"), ref="main")

    def tearDown(self):
        self.env.close()

    def test_reads_at_the_ref_commit_and_ignores_the_working_tree(self):
        write(self.repo, "docs/a.md", "# A changed without commit\n")
        write(self.repo, "docs/new.md", "# Not committed\n")
        s = scan_git(self.source, 100_000)
        self.assertTrue(s.available)
        self.assertEqual(self.commit, s.ref_commit)
        self.assertEqual(["README.md", "docs/a.md"], sorted(f.path for f in s.files))
        file = next(f for f in s.files if f.path == "docs/a.md")
        self.assertIsNone(file.content)  # only read when needed
        load(self.source, file, s.ref_commit)
        self.assertEqual(b"# A\n", file.content)
        self.assertEqual(sha256(b"# A\n"), file.hash)
        self.assertIsNotNone(file.modified_at)

    def test_does_not_touch_the_repository(self):
        index = self.repo / ".git" / "index"
        before = (index.stat().st_mtime_ns, git(self.repo, "status", "--porcelain"), git(self.repo, "rev-parse", "HEAD"))
        s = scan_git(self.source, 100_000)
        for file in s.files:
            load(self.source, file, s.ref_commit)
        after = (index.stat().st_mtime_ns, git(self.repo, "status", "--porcelain"), git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(before, after)

    def test_unknown_ref_makes_the_source_unavailable(self):
        source = Source("acme-api", "Acme API", "git", self.repo, ("*.md",), ref="origin/does-not-exist")
        s = scan_git(source, 100_000)
        self.assertFalse(s.available)
        self.assertTrue(s.warnings)


if __name__ == "__main__":
    unittest.main()
