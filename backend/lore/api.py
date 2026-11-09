"""lore's internal API (/api/lore) and the compiled web UI.

Security:
- listens only on a local address (``config``) and refuses any Host header that is not local
  (prevents a web page from using DNS rebinding to read the index);
- the ONLY route that is not GET is ``POST /api/lore/reindex``, which only re-reads the sources and updates
  lore's own index. No route receives content or writes to .md files;
- ``reindex`` requires the ``X-Lore-Confirm: reindex`` header. A custom header forces the browser into a CORS
  preflight, which lore never authorizes, so another site cannot trigger a reindex through the browser;
- every reindex goes into the audit log, with the IP and agent that asked for it.
"""

import logging
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lore import __version__, audit
from lore.config import LOCAL_HOSTS, Settings, settings
from lore.decisions import STATES
from lore.indexer import IndexingInProgress, RunContext
from lore.search import InvalidQuery, search
from lore.service import Lore

log = logging.getLogger("lore")

CONFIRM_HEADER = "x-lore-confirm"
CONFIRM_VALUE = "reindex"


class ReindexRequest(BaseModel):
    full: bool = False


class Syncer(threading.Thread):
    """Checks the sources every now and then and indexes only what changed."""

    def __init__(self, lore: Lore, interval: int) -> None:
        super().__init__(name="lore-sync", daemon=True)
        self.lore = lore
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                summary = self.lore.reindex(RunContext(origin="scheduled"))
                if summary.recorded:
                    log.info("Sync: %s", summary.totals)
            except IndexingInProgress:
                pass
            except Exception:  # never brings the server down
                log.exception("Automatic sync failed")
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()


class SPAFiles(StaticFiles):
    """Serves the compiled frontend; a path without an extension falls back to index.html (React routes)."""

    async def get_response(self, path: str, scope: Any) -> Any:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            parts = path.split("/")
            if error.status_code == 404 and "." not in parts[-1] and ".." not in parts and parts[0] != "api":
                response = await super().get_response("index.html", scope)
            else:
                raise
        if response.media_type == "text/html" or path in ("", ".", "index.html"):
            # The page always checks for a new version; assets have a hash in their name and may be cached.
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(cfg: Settings | None = None, sync: bool | None = None) -> FastAPI:
    cfg = cfg or settings()
    lore = Lore(cfg)
    run_sync = cfg.sync_seconds > 0 if sync is None else sync

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        lore.connect().close()  # creates/migrates the database before the first request
        syncer = None
        if run_sync:
            syncer = Syncer(lore, max(cfg.sync_seconds, 30))
            syncer.start()
        yield
        if syncer:
            syncer.stop()

    app = FastAPI(
        title="lore",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/lore/openapi.json",
    )
    app.state.lore = lore
    app.add_middleware(GZipMiddleware, minimum_size=2000)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(_routes())

    if cfg.frontend_dir.is_dir() and (cfg.frontend_dir / "index.html").is_file():
        app.mount("/", SPAFiles(directory=cfg.frontend_dir, html=True), name="ui")
    else:
        folder = cfg.frontend_dir

        @app.get("/", include_in_schema=False)
        def missing_ui() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>lore</title>"
                "<p>The web UI is not built. Run <code>scripts/lore.sh build</code> "
                f"(expected in <code>{folder}</code>). The API is at <code>/api/lore/status</code>.</p>"
            )

    return app


def _lore(request: Request) -> Lore:
    return request.app.state.lore


def _connection(request: Request) -> Iterator[Any]:
    conn = _lore(request).connect()
    try:
        yield conn
    finally:
        conn.close()


LoreDep = Annotated[Lore, Depends(_lore)]
ConnDep = Annotated[Any, Depends(_connection)]


def _routes() -> APIRouter:
    routes = APIRouter(prefix="/api/lore", tags=["lore"])

    @routes.get("/status")
    def status(lore: LoreDep, conn: ConnDep) -> dict[str, Any]:
        return lore.status(conn)

    @routes.get("/documents")
    def documents(
        lore: LoreDep,
        conn: ConnDep,
        source: str | None = Query(default=None, max_length=60),
        category: str | None = Query(default=None, max_length=200),
        status: str | None = Query(default=None, pattern="^(indexed|error|removed)$"),
        q: str | None = Query(default=None, max_length=100),
        include_removed: bool = False,
        limit: int = Query(default=1000, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return lore.documents(
            conn,
            source=source,
            category=category,
            status=status,
            q=q,
            include_removed=include_removed,
            limit=limit,
            offset=offset,
        )

    @routes.get("/documents/{document_id}")
    def document(document_id: int, lore: LoreDep, conn: ConnDep) -> dict[str, Any]:
        found = lore.document(conn, document_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return found

    @routes.get("/search")
    def search_route(
        lore: LoreDep,
        conn: ConnDep,
        q: str = Query(min_length=1, max_length=200),
        source: str | None = Query(default=None, max_length=60),
        category: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
        per_document: int = Query(default=3, ge=1, le=20),
        mode: str = Query(default="text", pattern="^(text|hybrid)$"),
    ) -> dict[str, Any]:
        try:
            result = search(
                conn, q, source=source, category=category, limit=limit, per_document=per_document,
                language=lore.language,
            )
        except InvalidQuery as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result["requested_mode"] = mode
        result["used_mode"] = "text"
        if mode == "hybrid":
            result["warning"] = "Semantic search is not configured yet; results are full-text only."
        return result

    @routes.post("/reindex")
    def reindex(
        request: Request,
        lore: LoreDep,
        body: Annotated[ReindexRequest | None, Body()] = None,
    ) -> JSONResponse:
        if request.headers.get(CONFIRM_HEADER) != CONFIRM_VALUE:
            raise HTTPException(
                status_code=403,
                detail=f"Send the header X-Lore-Confirm: {CONFIRM_VALUE} to reindex.",
            )
        context = RunContext(
            origin="api",
            ip=request.client.host if request.client else None,
            agent=request.headers.get("user-agent"),
        )
        try:
            summary = lore.reindex(context, full=bool(body and body.full))
        except IndexingInProgress as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JSONResponse(summary.as_dict())

    @routes.get("/graph")
    def graph(lore: LoreDep, conn: ConnDep) -> dict[str, Any]:
        return lore.graph_summary(conn)

    @routes.get("/graph/node")
    def graph_node(lore: LoreDep, conn: ConnDep, id: str = Query(min_length=1, max_length=600)) -> dict[str, Any]:
        found = lore.graph_node(conn, id)
        if found is None:
            raise HTTPException(status_code=404, detail="Node not found in the graph.")
        return found

    @routes.get("/graph/edge")
    def graph_edge(lore: LoreDep, conn: ConnDep, id: str = Query(min_length=1, max_length=1300)) -> dict[str, Any]:
        found = lore.graph_edge(conn, id)
        if found is None:
            raise HTTPException(status_code=404, detail="Edge not found in the graph.")
        return found

    @routes.get("/decisions")
    def decisions(
        lore: LoreDep,
        conn: ConnDep,
        q: str | None = Query(default=None, max_length=200),
        state: str | None = Query(default=None, pattern="^(" + "|".join(STATES) + ")$"),
    ) -> dict[str, Any]:
        return lore.decisions(conn, q, state)

    @routes.get("/context")
    def context(lore: LoreDep, conn: ConnDep, task: str = Query(min_length=1, max_length=500)) -> dict[str, Any]:
        try:
            return lore.context(conn, task)
        except InvalidQuery as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @routes.get("/mcp")
    def mcp(lore: LoreDep, conn: ConnDep) -> dict[str, Any]:
        from lore.mcp_server import available_tools

        command = str(Path(sys.executable).parent / "lore-mcp")
        return lore.mcp_panel(conn, available_tools(lore.cfg), command)

    @routes.get("/runs")
    def runs(lore: LoreDep, conn: ConnDep, limit: int = Query(default=30, ge=1, le=500)) -> list[dict[str, Any]]:
        return lore.runs(conn, limit)

    @routes.get("/audit")
    def audit_log(conn: ConnDep, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return audit.list_entries(conn, limit)

    return routes


def write_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Routes that accept something other than GET/HEAD — used by the tests to guarantee read-only."""
    out: list[tuple[str, str]] = []

    def walk(routes: Any) -> None:
        for route in routes:
            included = getattr(route, "original_router", None)  # FastAPI >= 0.140 wraps include_router
            if included is not None:
                walk(included.routes)
                continue
            methods = getattr(route, "methods", None) or set()
            for method in sorted(methods - {"GET", "HEAD"}):
                out.append((method, getattr(route, "path", "")))

    walk(app.routes)
    return out
