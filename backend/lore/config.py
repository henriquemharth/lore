"""Configuration from environment variables (prefix ``LORE_``) or a ``.env`` file in the project root.

Project root:
- the folder of ``LORE_SOURCES_FILE``, when that variable is set;
- the current directory, when it contains a ``sources.yaml`` (e.g. after ``lore init``);
- otherwise, in a source checkout (``<root>/backend/lore``), the parent of ``backend/``;
- otherwise the current directory.
Every default path below is relative to that root (except the built UI, which ships with lore);
each one can be overridden by its variable.
"""

import os
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lore.sources import DEFAULT_DOMAIN


def _project_root() -> Path:
    # MCP clients often start the server outside the project and point at it with this variable.
    sources_file = os.environ.get("LORE_SOURCES_FILE")
    if sources_file:
        return Path(sources_file).expanduser().resolve().parent
    cwd = Path.cwd()
    if (cwd / "sources.yaml").is_file():
        return cwd
    package = Path(__file__).resolve().parent
    if package.parent.name == "backend" and (package.parent / "pyproject.toml").is_file():
        return package.parents[1]
    return cwd


PROJECT_ROOT = _project_root()

# The built UI ships with lore itself (<checkout>/frontend/dist), not with the project being indexed.
_PACKAGE_DIR = Path(__file__).resolve().parent
INSTALL_ROOT = _PACKAGE_DIR.parents[1] if _PACKAGE_DIR.parent.name == "backend" else PROJECT_ROOT

# Host names accepted in the Host header. Anything else is refused (protection against DNS rebinding).
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LORE_", env_file=PROJECT_ROOT / ".env", extra="ignore")

    sources_file: Path = PROJECT_ROOT / "sources.yaml"
    taxonomy_file: Path = PROJECT_ROOT / "taxonomy.yaml"
    privacy_file: Path = PROJECT_ROOT / "privacy.yaml"
    data_dir: Path = PROJECT_ROOT / ".lore"
    frontend_dir: Path = INSTALL_ROOT / "frontend" / "dist"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8610, ge=1024, le=65535)
    # Automatic source check, in seconds. 0 disables it; values below 30 are refused.
    sync_seconds: int = Field(default=300, ge=0)
    # Query-side word folding: "en" (English plurals/suffixes) or "pt" (Portuguese rules).
    search_language: Literal["en", "pt"] = "en"

    # Limits on what the MCP server hands to an AI client (token control).
    mcp_max_chunks: int = Field(default=8, ge=1, le=50)
    mcp_max_chars_per_chunk: int = Field(default=1200, ge=200, le=10_000)
    mcp_max_chars_total: int = Field(default=12_000, ge=1_000, le=100_000)
    mcp_max_documents: int = Field(default=6, ge=1, le=50)
    mcp_min_score: float = Field(default=0.3, ge=0.0, le=1.0)  # relative to the best result
    mcp_max_search_results: int = Field(default=20, ge=1, le=100)

    # Domain wall: an MCP process only sees the sources of this domain (the "domain" field of
    # sources.yaml). Each project registers its own.
    mcp_domain: str = Field(default=DEFAULT_DOMAIN, pattern=r"^[a-z][a-z0-9-]{1,20}$")

    # Memories written through MCP (lore_remember): each one becomes a NEW .md in this folder, which must
    # be the root of a source of the same domain in sources.yaml to be indexed. No existing .md is changed.
    # Without LORE_MEMORIES_DIR the default domain uses memories/ and the others memories-<domain>/.
    memories_dir: Path = PROJECT_ROOT / "memories"
    mcp_allow_memory: bool = True
    mcp_max_memories_per_hour: int = Field(default=30, ge=1, le=1000)
    mcp_max_chars_per_memory: int = Field(default=4000, ge=200, le=20_000)
    # lore_forget: moves a memory created through MCP to memories/.trash/ (recoverable); other sources are
    # never touched. Forgotten memories have their own counter, with the same hourly cap.
    mcp_allow_forget: bool = True
    # After writing, ask the local lore server (POST /api/lore/reindex) to index right away.
    mcp_memory_reindex: bool = True

    @field_validator("http_host")
    @classmethod
    def _local_only(cls, value: str) -> str:
        if value == "localhost":
            return value
        try:
            local = ip_address(value.strip("[]")).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("lore only listens on a local address (127.0.0.1, ::1 or localhost).")
        return value

    @field_validator("sync_seconds")
    @classmethod
    def _min_interval(cls, value: int) -> int:
        if 0 < value < 30:
            raise ValueError("LORE_SYNC_SECONDS must be 0 (disabled) or at least 30.")
        return value

    @model_validator(mode="after")
    def _domain_memories(self) -> "Settings":
        if "memories_dir" not in self.model_fields_set and self.mcp_domain != DEFAULT_DOMAIN:
            self.memories_dir = PROJECT_ROOT / f"memories-{self.mcp_domain}"
        return self

    @property
    def database(self) -> Path:
        return self.data_dir / "lore.sqlite3"


@lru_cache
def settings() -> Settings:
    return Settings()
