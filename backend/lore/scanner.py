"""Discovery of the .md files of each source — READ ONLY.

Nothing here opens a file for writing, changes permissions, moves or renames. Git sources are read at the
commit of the ref (``ls-tree``/``cat-file``/``log``), without touching the working tree or the index of
the repository, and without ``fetch``.
"""

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from lore.privacy import Privacy
from lore.sources import Source


class GitError(RuntimeError):
    pass


@dataclass
class DiscoveredFile:
    source_id: str
    path: str  # relative to the source root, POSIX
    name: str
    size: int
    # Identity used to detect changes without reprocessing: sha256 (folder) or "git:<blob>" (git).
    identity: str
    # sha256 of the content; for git sources only known after load().
    hash: str | None = None
    modified_at: str | None = None
    content: bytes | None = None


@dataclass
class Scan:
    source: Source
    available: bool
    files: list[DiscoveredFile] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, message)
    warnings: list[str] = field(default_factory=list)
    ref_commit: str | None = None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso_utc(instant: float) -> str:
    return datetime.fromtimestamp(instant, UTC).isoformat(timespec="seconds")


def scan(sources: tuple[Source, ...], max_bytes: int, privacy: Privacy | None = None) -> list[Scan]:
    return [
        scan_folder(s, max_bytes, privacy) if s.type == "folder" else scan_git(s, max_bytes, privacy)
        for s in sources
    ]


def scan_folder(source: Source, max_bytes: int, privacy: Privacy | None = None) -> Scan:
    root = source.root
    if not root.is_dir():
        return Scan(source, available=False, warnings=[f"Folder not found: {root}"])
    real_root = root.resolve()
    result = Scan(source, available=True)

    candidates: list[tuple[str, str, Path]] = []
    for folder, subfolders, files in os.walk(real_root, followlinks=False):
        # Prune hidden folders (.obsidian, .git, .trash…) before descending into them.
        subfolders[:] = sorted(d for d in subfolders if not d.startswith("."))
        rel_folder = PurePosixPath(Path(folder).relative_to(real_root).as_posix())
        for name in sorted(files):
            rel = (rel_folder / name).as_posix() if str(rel_folder) != "." else name
            if source.accepts(rel):
                candidates.append((rel, name, Path(folder) / name))

    denied = [rel for rel, _, _ in candidates if privacy and privacy.denied(rel)]
    ignored = _git_ignored(real_root, [rel for rel, _, _ in candidates]) if source.respect_gitignore else set()
    if denied:
        result.warnings.append(f"{len(denied)} file(s) left out of the index by the privacy deny list.")
    if ignored:
        result.warnings.append(f"{len(ignored)} file(s) left out of the index by the source's .gitignore.")
    for rel, name, path in candidates:
        if rel in ignored or rel in denied:
            continue
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = path.resolve()
                if not target.is_relative_to(real_root):
                    result.warnings.append(f"{rel}: symbolic link pointing outside the source, ignored.")
                    continue
                info = target.stat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if info.st_size > max_bytes:
                result.errors.append((rel, f"File has {info.st_size} bytes, over the limit of {max_bytes}."))
                continue
            with open(path, "rb") as reader:  # read only
                data = reader.read(max_bytes + 1)
        except OSError as error:
            result.errors.append((rel, f"Could not read: {error.strerror or error}"))
            continue
        if len(data) > max_bytes:
            result.errors.append((rel, f"File is over the limit of {max_bytes} bytes."))
            continue
        digest = sha256(data)
        result.files.append(
            DiscoveredFile(
                source_id=source.id,
                path=rel,
                name=name,
                size=len(data),
                identity=digest,
                hash=digest,
                modified_at=_iso_utc(info.st_mtime),
                content=data,
            )
        )
    return result


def _git_ignored(root: Path, paths: list[str]) -> set[str]:
    """Paths that the .gitignore of the source's repository says to ignore (read only, no index)."""
    if not paths:
        return set()
    try:
        if _git(root, "rev-parse", "--is-inside-work-tree").strip() != b"true":
            return set()
    except GitError:
        return set()
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        process = subprocess.run(
            ["git", "-C", str(root), "--no-pager", "check-ignore", "--stdin", "-z"],
            input=b"\0".join(p.encode("utf-8") for p in paths) + b"\0",
            capture_output=True,
            env=env,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if process.returncode not in (0, 1):
        return set()
    return {p.decode("utf-8") for p in process.stdout.split(b"\0") if p}


def _git(repository: Path, *arguments: str) -> bytes:
    env = {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",  # no read command updates the index
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    try:
        process = subprocess.run(
            ["git", "-C", str(repository), "--no-pager", *arguments],
            capture_output=True,
            env=env,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git failed: {error}") from error
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", "replace").strip() or f"exit code {process.returncode}"
        raise GitError(message)
    return process.stdout


def scan_git(source: Source, max_bytes: int, privacy: Privacy | None = None) -> Scan:
    repository = source.root
    if not repository.exists():
        return Scan(source, available=False, warnings=[f"Repository not found: {repository}"])
    try:
        commit = _git(repository, "rev-parse", "--verify", "--quiet", f"{source.ref}^{{commit}}").decode().strip()
        listing = _git(repository, "ls-tree", "-r", "-l", "-z", "--full-tree", commit)
    except GitError as error:
        return Scan(source, available=False, warnings=[f"Ref {source.ref} unavailable: {error}"])

    result = Scan(source, available=True, ref_commit=commit)
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        header, _, raw_path = entry.partition(b"\t")
        mode, kind, blob, size = header.split()
        if kind != b"blob":
            continue
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            result.warnings.append("Path with invalid encoding ignored.")
            continue
        if not source.accepts(path):
            continue
        if privacy and privacy.denied(path):
            result.warnings.append(f"{path}: left out of the index by the privacy deny list.")
            continue
        if mode == b"120000":
            result.warnings.append(f"{path}: symbolic link in the repository, ignored.")
            continue
        size_int = int(size)
        if size_int > max_bytes:
            result.errors.append((path, f"File has {size_int} bytes, over the limit of {max_bytes}."))
            continue
        result.files.append(
            DiscoveredFile(
                source_id=source.id,
                path=path,
                name=PurePosixPath(path).name,
                size=size_int,
                identity=f"git:{blob.decode()}",
            )
        )
    return result


def load(source: Source, file: DiscoveredFile, commit: str | None) -> DiscoveredFile:
    """Ensures content, hash and modification date (git sources only read when needed)."""
    if file.content is not None:
        return file
    if source.type != "git" or commit is None:
        raise GitError("File without loaded content.")
    blob = file.identity.removeprefix("git:")
    data = _git(source.root, "cat-file", "blob", blob)
    date = _git(source.root, "log", "-1", "--format=%cI", commit, "--", file.path).decode().strip()
    file.content = data
    file.hash = sha256(data)
    file.size = len(data)
    if date:
        file.modified_at = datetime.fromisoformat(date).astimezone(UTC).isoformat(timespec="seconds")
    return file
