"""Command line: ``lore init | index | status | search | serve | mcp | reset-mcp-usage``."""

import argparse
import json
import sys
from pathlib import Path

from lore.indexer import IndexingInProgress, RunContext
from lore.search import MARK_END, MARK_START, InvalidQuery, search
from lore.sources import ConfigError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lore", description="lore: a local, traceable project memory for AI coding agents."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Writes starter sources.yaml, taxonomy.yaml and privacy.yaml here (if missing).")

    p_index = sub.add_parser("index", help="Re-reads the sources and updates the index (only what changed).")
    p_index.add_argument("--full", action="store_true", help="Reprocess every file, even unchanged ones.")

    p_status = sub.add_parser("status", help="Summary of the index and of what changed in the sources.")
    p_status.add_argument("--json", action="store_true", help="JSON output.")

    p_search = sub.add_parser("search", help="Full-text search in the index.")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)

    sub.add_parser("mcp", help="MCP server over stdio (started by the AI client); same as lore-mcp.")
    sub.add_parser("reset-mcp-usage", help="Resets the counters of the MCP panel (the audit log stays complete).")

    p_serve = sub.add_parser("serve", help="Starts the API and the web UI on 127.0.0.1.")
    p_serve.add_argument("--no-sync", action="store_true", help="Do not check the sources automatically.")

    args = parser.parse_args(argv)
    if args.command == "init":
        return _init()
    try:
        from lore.config import settings
        from lore.service import Lore

        cfg = settings()
        if args.command == "serve":
            return _serve(cfg, not args.no_sync)
        if args.command == "mcp":
            from lore.mcp_server import main as mcp_main

            return mcp_main()
        lore = Lore(cfg)
    except (ConfigError, ValueError) as error:
        print(f"Invalid configuration: {error}", file=sys.stderr)
        return 2

    if args.command == "index":
        try:
            summary = lore.reindex(RunContext(origin="cli"), full=args.full)
        except IndexingInProgress as error:
            print(error, file=sys.stderr)
            return 1
        t = summary.totals
        print(
            f"Run {summary.run_id} completed in {summary.duration_ms} ms: "
            f"{t['documents']} documents, {t['chunks']} chunks | new {t['new']}, changed {t['changed']}, "
            f"reprocessed {t['reprocessed']}, restored {t['restored']}, removed {t['removed']}, "
            f"errors {t['errors']}, reclassified {t['reclassified']}, unchanged {t['unchanged']}."
        )
        for source in summary.unavailable_sources:
            print(f"  warning: source unavailable: {source}")
        for error in summary.errors:
            print(f"  error: {error['source_id']}:{error['path']} — {error['error']}")
        return 0 if not summary.errors else 1

    conn = lore.connect()
    try:
        if args.command == "status":
            s = lore.status(conn)
            if args.json:
                print(json.dumps(s, ensure_ascii=False, indent=2))
                return 0
            last = s["last_run"] or {}
            docs = s["documents"]
            print(f"Documents: {docs['total']} (errors {docs['with_errors']}, removed {docs['removed']})")
            print(f"Chunks: {s['chunks']}  |  Links: {s['links']['resolved']}/{s['links']['total']} resolved")
            print(f"Last run: {last.get('finished_at') or 'never'} ({last.get('origin', '-')})")
            print(f"Pending changes in the sources: {len(s['pending'])}")
            for p in s["pending"][:20]:
                print(f"  {p['type']:>9}  {p['source_id']}:{p['path']}")
            for src in s["sources"]:
                state = "ok" if src["available"] else "unavailable" if src["available"] is False else "not checked"
                print(f"  source {src['id']} [{src['domain']}]: {src['documents']} documents, {state}")
            return 0
        if args.command == "reset-mcp-usage":
            from lore import audit
            from lore.db import set_meta

            last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit").fetchone()[0]
            set_meta(conn, "mcp_usage_since_id", str(last_id))
            audit.record(conn, "mcp_usage_reset", "cli", {"from_entry": last_id})
            print(f"MCP panel reset: it only counts what comes after audit entry {last_id}.")
            return 0
        if args.command == "search":
            try:
                r = search(conn, args.query, limit=args.limit, language=lore.language)
            except InvalidQuery as error:
                print(error, file=sys.stderr)
                return 2
            print(f"{r['total_documents']} documents ({r['mode']}): {r['expression']}")
            for item in r["results"]:
                excerpt = item["excerpt"].replace(MARK_START, "[").replace(MARK_END, "]").replace("\n", " ")
                print(f"- {item['source_id']}:{item['path']}:{item['line_start']}  {item['title']}\n    {excerpt}")
            return 0
    finally:
        conn.close()
    return 0


def _init() -> int:
    from lore.scaffold import init

    here = Path.cwd()
    for name, state in init(here):
        print(f"{'created' if state == 'created' else 'kept (already exists)'}: {here / name}")
    print(
        "Next: review the files, then run 'lore index' and 'lore serve' here "
        "(the index goes to ./.lore — add it and ./memories to .gitignore if you like)."
    )
    return 0


def _serve(cfg, sync: bool) -> int:  # noqa: ANN001
    import os

    import uvicorn

    from lore.api import create_app

    if not sync:  # --no-sync: report the automatic check as off (status shows sync_seconds = 0)
        cfg = cfg.model_copy(update={"sync_seconds": 0})
    # The server writes its own PID: on macOS the Python executable may be a launcher that starts the real
    # process with another PID, and the stop script needs the PID of whoever listens on the port.
    cfg.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    pid_file = cfg.data_dir / "lore.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    print(f"lore at http://{cfg.http_host}:{cfg.http_port} (read only; Ctrl+C to stop)")
    try:
        uvicorn.run(
            create_app(cfg, sync=sync and cfg.sync_seconds > 0),
            host=cfg.http_host,
            port=cfg.http_port,
            proxy_headers=False,
            server_header=False,
            log_level="info",
        )
    finally:
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_file.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
