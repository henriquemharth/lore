"""Test support: temporary folders, a throwaway git repository and a real HTTP server.

Fixtures describe "Acme Shop", a fictional e-commerce project.
"""

import http.client
import json
import os
import subprocess
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from lore.categories import load_taxonomy
from lore.config import Settings
from lore.indexer import Indexer
from lore.sources import Source, SourcesConfig

TAXONOMY = Path(__file__).resolve().parent / "fixtures" / "taxonomy.yaml"


def write(root: Path, path: str, text: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return target


def git(folder: Path, *arguments: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(
        ["git", "-C", str(folder), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", *arguments],
        check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


def create_repository(folder: Path, files: dict[str, str]) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "-q", "-b", "main")
    for path, text in files.items():
        write(folder, path, text)
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "initial")
    return git(folder, "rev-parse", "HEAD")


class Env:
    """A temporary notes folder and a temporary database per test."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="lore-test-")
        self.base = Path(self._tmp.name)
        self.notes = self.base / "notes"
        self.notes.mkdir()
        self.data = self.base / "data"
        self.db = self.data / "lore.sqlite3"
        self.memories = self.base / "memories"

    def notes_source(self) -> Source:
        return Source(id="notes", name="Acme Shop notes", type="folder", root=self.notes, include=("**/*.md",))

    def sources(self, *extra: Source, max_bytes: int = 100_000) -> SourcesConfig:
        return SourcesConfig(sources=(self.notes_source(), *extra), max_bytes=max_bytes)

    def indexer(self, sources: SourcesConfig | None = None) -> Indexer:
        return Indexer(self.db, sources or self.sources(), load_taxonomy(TAXONOMY))

    def settings(
        self, frontend: Path | None = None, privacy: str | None = None, memories: bool = False, **extra: object
    ) -> Settings:
        """Test settings. The memories folder is always the temporary one and the MCP server never calls a lore
        server on port 8610; ``memories=True`` adds the ``memories`` source."""
        file = self.base / "sources.yaml"
        text = (
            "sources:\n"
            f"  - id: notes\n    name: Acme Shop notes\n    type: folder\n    root: {self.notes}\n"
            "    include: ['**/*.md']\n"
        )
        if memories:
            text += (
                f"  - id: memories\n    name: MCP memories\n    type: folder\n    root: {self.memories}\n"
                "    include: ['*.md']\n    respect_gitignore: false\n"
            )
        file.write_text(text, encoding="utf-8")
        privacy_file = self.base / "privacy.yaml"
        privacy_file.write_text(privacy or "deny: []\nsensitive: []\n", encoding="utf-8")
        return Settings(
            sources_file=file,
            taxonomy_file=TAXONOMY,
            privacy_file=privacy_file,
            data_dir=self.data,
            frontend_dir=frontend or self.base / "no-frontend",
            sync_seconds=0,
            **{"memories_dir": self.memories, "mcp_memory_reindex": False, **extra},
        )

    def process_env(self, cfg: Settings) -> dict[str, str]:
        """Variables to start the MCP server as a process pointing at this environment."""
        return {
            "LORE_SOURCES_FILE": str(cfg.sources_file),
            "LORE_TAXONOMY_FILE": str(cfg.taxonomy_file),
            "LORE_PRIVACY_FILE": str(cfg.privacy_file),
            "LORE_DATA_DIR": str(cfg.data_dir),
            "LORE_MEMORIES_DIR": str(cfg.memories_dir),
            "LORE_MCP_MEMORY_REINDEX": "true" if cfg.mcp_memory_reindex else "false",
            "LORE_MCP_ALLOW_MEMORY": "true" if cfg.mcp_allow_memory else "false",
        }

    def close(self) -> None:
        self._tmp.cleanup()


class Server:
    """Runs the app on a real uvicorn on a free port (the tests go through the middlewares)."""

    def __init__(self, app: Any) -> None:
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = 0

    def __enter__(self) -> "Server":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Test server did not start.")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.should_exit = True
        self.thread.join(10)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = None
        all_headers = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if body is not None:
            data = json.dumps(body).encode()
            all_headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=all_headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        kind = response.getheader("content-type", "")
        content: Any = json.loads(raw) if "json" in kind and raw else raw.decode("utf-8", "replace")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, content
