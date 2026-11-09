"""``lore init``: starter sources.yaml, taxonomy.yaml and privacy.yaml for the current project.

Existing files are never overwritten. In a git repository the main source is the repository itself, read at
``HEAD`` (``*.md`` and ``docs/**/*.md``); elsewhere it is the ``./docs`` folder. A second source reads
``./memories``, where the MCP server writes the memories created by ``lore_remember``.
"""

import json
import subprocess
from pathlib import Path

from lore.privacy import DEFAULT_DENY

FILES = ("sources.yaml", "taxonomy.yaml", "privacy.yaml")


def _is_git_repository(directory: Path) -> bool:
    try:
        process = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.returncode == 0 and process.stdout.strip() == b"true"


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a JSON string is a valid YAML scalar


def sources_yaml(directory: Path, git: bool) -> str:
    project = directory.resolve().name or "project"
    if git:
        main = (
            "  - id: repo\n"
            f"    name: {_quote(f'{project} repository (HEAD)')}\n"
            "    type: git\n"
            "    root: .\n"
            "    ref: HEAD\n"
            '    include: ["*.md", "docs/**/*.md"]\n'
        )
    else:
        main = (
            "  - id: docs\n"
            f"    name: {_quote(f'{project} docs')}\n"
            "    type: folder\n"
            "    root: ./docs\n"
            '    include: ["**/*.md"]\n'
        )
    return (
        "# lore sources. Only .md files are read; hidden files and folders are skipped.\n"
        "# A relative root resolves from this file's folder. Git sources are read at the commit of 'ref'\n"
        "# (git ls-tree / cat-file), never from the working tree.\n"
        "max_bytes_per_file: 2000000\n"
        "\n"
        "# Each source belongs to a domain (default: \"default\"); an MCP process only sees its own domain\n"
        "# (LORE_MCP_DOMAIN). The description goes into the MCP server instructions.\n"
        "domains:\n"
        "  default:\n"
        f"    description: {_quote(f'of the {project} project')}\n"
        "\n"
        "# Decision records: codes in file names and text, and numbered files such as adr/0001-title.md.\n"
        "# decisions:\n"
        '#   patterns: ["DEC-\\\\d{3,4}", "ADR-\\\\d{3,4}"]\n'
        "#   numbered_folders: [adr, adrs, decisions, decision-records]\n"
        "\n"
        "sources:\n"
        f"{main}"
        "\n"
        "  - id: memories\n"
        "    name: Agent memories (created by lore_remember)\n"
        "    type: folder\n"
        "    root: ./memories\n"
        '    include: ["*.md"]\n'
        "    respect_gitignore: false\n"
    )


def taxonomy_yaml(directory: Path) -> str:
    project = directory.resolve().name or "Project"
    return (
        "# Knowledge tree and classification rules.\n"
        "# Every category assigned to a document records WHERE it came from:\n"
        "#   explicit   — the source itself says so (frontmatter category/categories/tags, the source, the folder,\n"
        "#                a file name that names the subject);\n"
        "#   suggestion — an interpretation: a rule marked \"suggestion\" or a keyword (tags) in the file name,\n"
        "#                title or a heading.\n"
        "# rules: the FIRST matching rule sets the primary category; confidence and reason are required.\n"
        "# tags: extra categories by word (always suggestions); a word matches the start of a word, accent- and\n"
        "#       case-insensitive (\"deploy\" finds \"deployment\").\n"
        f"root: {_quote(project)}\n"
        "\n"
        "categories:\n"
        "  - Architecture\n"
        "  - Decisions\n"
        "  - Operations\n"
        "  - Guides\n"
        "  - Memories\n"
        "\n"
        "rules:\n"
        "  - category: Memories\n"
        "    sources: [memories]\n"
        "    confidence: explicit\n"
        "    reason: the source is the folder of AI agent memories\n"
        "  - category: Decisions\n"
        '    paths: ["adr/*.md", "adrs/*.md", "decisions/*.md", "docs/adr/*.md", "docs/decisions/*.md"]\n'
        "    confidence: explicit\n"
        "    reason: the folder holds decision records\n"
        "  - category: Architecture\n"
        '    paths: ["**/ARCHITECTURE.md", "docs/architecture/**/*.md"]\n'
        "    confidence: explicit\n"
        "    reason: the file or folder name says architecture\n"
        "  - category: Operations\n"
        '    paths: ["docs/runbooks/**/*.md", "docs/operations/**/*.md"]\n'
        "    confidence: explicit\n"
        "    reason: the folder holds runbooks / operations docs\n"
        "\n"
        "tags:\n"
        "  - category: Architecture\n"
        "    words: [architecture, design, component, module, stack, diagram]\n"
        "  - category: Decisions\n"
        "    words: [decision, adr]\n"
        "  - category: Operations\n"
        "    words: [deploy, release, runbook, incident, monitoring, backup, infrastructure, server, docker]\n"
        "  - category: Guides\n"
        "    words: [guide, howto, tutorial, setup, install, onboarding]\n"
    )


def privacy_yaml() -> str:
    deny = "".join(f"  - {_quote(p)}\n" for p in DEFAULT_DENY)
    return (
        "# deny: files lore NEVER reads nor returns, in any source (matched by file name and path,\n"
        "#       case-insensitive). These defaults always apply; entries here are added to them.\n"
        "# sensitive: documents that are indexed and visible in the local web UI, but never delivered\n"
        "#       through MCP to an AI client (no excerpt, title or path).\n"
        "#   - sources: [docs]\n"
        "#     paths: [\"people/*.md\"]\n"
        "#     reason: personal data\n"
        "# Every text that leaves through MCP also goes through secret redaction.\n"
        f"deny:\n{deny}"
        "\n"
        "sensitive: []\n"
    )


def init(directory: Path) -> list[tuple[str, str]]:
    """Writes the starter files that are missing. Returns (file name, "created" | "exists")."""
    directory = Path(directory)
    contents = {
        "sources.yaml": sources_yaml(directory, _is_git_repository(directory)),
        "taxonomy.yaml": taxonomy_yaml(directory),
        "privacy.yaml": privacy_yaml(),
    }
    result = []
    for name in FILES:
        target = directory / name
        if target.exists():
            result.append((name, "exists"))
            continue
        with open(target, "x", encoding="utf-8") as out:  # "x": never overwrites
            out.write(contents[name])
        result.append((name, "created"))
    if ("sources.yaml", "created") in result:
        # The starter sources.yaml lists ./memories; create it so the first index has no warning.
        (directory / "memories").mkdir(exist_ok=True)
    return result
