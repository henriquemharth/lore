# Contributing to lore

Thanks for helping! A few ground rules keep lore trustworthy:

1. **Sources are read-only.** No code path may write, move or rename a file inside a configured
   source. The only writes are lore's own index (`.lore/`) and new files created by `lore_remember`.
2. **Everything is traceable.** Anything lore tells an agent — a chunk, a graph edge, a decision
   state, a category — must carry the evidence it came from. If it can't, it doesn't ship.
3. **Written, not guessed.** Relations and states come from text in the documents, not from
   similarity or heuristics that can't point to a line.
4. **Local only.** No network calls besides `127.0.0.1`, no telemetry.

## Development

```bash
scripts/lore.sh build     # uv sync + npm ci + UI build
scripts/lore.sh test      # backend unittest + frontend type-check and node --test
```

- Backend: `backend/lore/` (Python 3.12, FastAPI, SQLite FTS5, MCP SDK). Tests in `backend/tests/`.
- UI: `frontend/src/` (React 18, Vite, Tailwind). `cd frontend && npm run dev` proxies `/api` to a
  running lore on port 8610.

Please add a test with every behaviour change and keep PRs focused.
