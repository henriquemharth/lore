"""``lore init``: starter files that load, work end to end and never overwrite anything."""

import contextlib
import io
import os
import unittest
from pathlib import Path

from support import Env, create_repository, write

from lore.categories import load_taxonomy
from lore.cli import main
from lore.indexer import Indexer, RunContext
from lore.privacy import DEFAULT_DENY, load_privacy
from lore.scaffold import init
from lore.sources import load_sources


@contextlib.contextmanager
def inside(folder: Path):
    previous = Path.cwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(previous)


class TestInit(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_plain_folder_gets_a_docs_source_and_the_files_load(self):
        project = self.env.base / "acme-shop"
        project.mkdir()
        self.assertEqual(
            [("sources.yaml", "created"), ("taxonomy.yaml", "created"), ("privacy.yaml", "created")], init(project)
        )
        sources = load_sources(project / "sources.yaml")
        by_id = {s.id: s for s in sources.sources}
        self.assertEqual({"docs", "memories"}, set(by_id))
        self.assertTrue((project / "memories").is_dir())
        self.assertEqual(("folder", project.resolve() / "docs"), (by_id["docs"].type, by_id["docs"].root))
        self.assertEqual(project.resolve() / "memories", by_id["memories"].root)
        self.assertFalse(by_id["memories"].respect_gitignore)
        self.assertEqual("of the acme-shop project", sources.describe("default"))
        taxonomy = load_taxonomy(project / "taxonomy.yaml")
        self.assertEqual("acme-shop", taxonomy.root)
        for category in ("Architecture", "Decisions", "Operations", "Memories"):
            self.assertIn(category, taxonomy.categories)
        privacy = load_privacy(project / "privacy.yaml")
        self.assertEqual(DEFAULT_DENY, privacy.deny)
        for name in (".env", ".env.production", "server.pem", "id_rsa", "my-secret.md", "aws-credentials.md"):
            self.assertTrue(privacy.denied(name), name)

    def test_git_repository_gets_a_git_source_at_head(self):
        project = self.env.base / "acme-api"
        create_repository(project, {"README.md": "# Acme API\n", "docs/adr/0001-use-fastapi.md": "# Use FastAPI\n\nStatus: Accepted\n"})
        init(project)
        [repo, memories] = load_sources(project / "sources.yaml").sources
        self.assertEqual(("repo", "git", "HEAD", project.resolve()), (repo.id, repo.type, repo.ref, repo.root))
        self.assertEqual(("*.md", "docs/**/*.md"), repo.include)
        self.assertEqual("memories", memories.id)
        # the starter files index the repository end to end
        db = self.env.base / "data" / "lore.sqlite3"
        indexer = Indexer(db, load_sources(project / "sources.yaml"), load_taxonomy(project / "taxonomy.yaml"))
        summary = indexer.run(RunContext(origin="test"))
        self.assertEqual(2, summary.totals["new"])
        self.assertEqual([], summary.unavailable_sources)  # init creates ./memories up front
        from contextlib import closing

        from lore.db import connect

        with closing(connect(db)) as conn:
            row = conn.execute("SELECT category FROM document WHERE path = 'docs/adr/0001-use-fastapi.md'").fetchone()
        self.assertEqual("Decisions", row["category"])

    def test_existing_files_are_never_overwritten(self):
        project = self.env.base / "kept"
        project.mkdir()
        write(project, "taxonomy.yaml", "root: Mine\n")
        result = dict(init(project))
        self.assertEqual("exists", result["taxonomy.yaml"])
        self.assertEqual("created", result["sources.yaml"])
        self.assertEqual("root: Mine\n", (project / "taxonomy.yaml").read_text(encoding="utf-8"))
        self.assertEqual({"sources.yaml": "exists", "taxonomy.yaml": "exists", "privacy.yaml": "exists"}, dict(init(project)))

    def test_cli_init_writes_into_the_current_directory(self):
        project = self.env.base / "cli-project"
        project.mkdir()
        out = io.StringIO()
        with inside(project), contextlib.redirect_stdout(out):
            self.assertEqual(0, main(["init"]))
        self.assertTrue((project / "sources.yaml").is_file())
        self.assertIn("created", out.getvalue())
        self.assertIn("lore index", out.getvalue())


if __name__ == "__main__":
    unittest.main()
