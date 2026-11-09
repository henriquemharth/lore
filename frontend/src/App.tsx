import { BookMarked, Cable, FolderTree, LayoutDashboard, Lock, Network, Search, ShieldCheck } from "lucide-react";
import { Suspense, lazy, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { AuditPage } from "./pages/AuditPage.tsx";
import { DashboardPage } from "./pages/DashboardPage.tsx";
import { ExplorerPage } from "./pages/ExplorerPage.tsx";
import { McpPage } from "./pages/McpPage.tsx";
import { SearchPage } from "./pages/SearchPage.tsx";

// The graph (Cytoscape) is the heaviest part of the UI: it only loads when its page is opened.
const GraphPage = lazy(() => import("./pages/GraphPage.tsx").then((m) => ({ default: m.GraphPage })));

const NAVIGATION = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { path: "/explorer", label: "Explorer", icon: FolderTree, end: false },
  { path: "/graph", label: "Graph", icon: Network, end: false },
  { path: "/search", label: "Search", icon: Search, end: false },
  { path: "/mcp", label: "MCP", icon: Cable, end: false },
  { path: "/audit", label: "Audit", icon: ShieldCheck, end: false },
];

export function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <main className="flex min-h-0 flex-1 flex-col p-3">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/explorer" element={<ExplorerPage />} />
          <Route path="/explorer/:id" element={<ExplorerPage />} />
          <Route
            path="/graph"
            element={
              <Suspense fallback={<p className="p-4 text-fg-3">Loading the graph…</p>}>
                <GraphPage />
              </Suspense>
            }
          />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/mcp" element={<McpPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Header() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const currentQuery = location.pathname === "/search" ? (params.get("q") ?? "") : "";
  const [query, setQuery] = useState(currentQuery);
  useEffect(() => setQuery(currentQuery), [currentQuery]);

  const search = (e: FormEvent) => {
    e.preventDefault();
    const clean = query.trim();
    if (clean) navigate(`/search?q=${encodeURIComponent(clean)}`);
  };

  return (
    <header className="sticky top-0 z-10 flex h-12 items-center gap-4 border-b border-line bg-surface px-3">
      <NavLink to="/" className="flex items-center gap-2 font-semibold text-fg">
        <span className="flex h-7 w-7 items-center justify-center rounded-[5px] bg-brand text-white">
          <BookMarked size={16} aria-hidden />
        </span>
        lore
      </NavLink>
      <nav aria-label="Sections" className="flex h-full items-stretch gap-1">
        {NAVIGATION.map(({ path, label, icon: Icon, end }) => (
          <NavLink
            key={path}
            to={path}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-1.5 border-b-2 px-2 font-medium ${
                isActive ? "border-brand text-brand" : "border-transparent text-fg-3 hover:text-fg"
              }`
            }
          >
            <Icon size={14} aria-hidden />
            <span className="hidden sm:inline">{label}</span>
          </NavLink>
        ))}
      </nav>
      <form role="search" onSubmit={search} className="relative ml-auto w-full max-w-[380px]">
        <span className="sr-only">Search everything</span>
        <Search size={14} className="absolute top-2 left-2 text-fg-3" aria-hidden />
        <input
          className="field w-full pl-7"
          placeholder="Search all project memory…"
          aria-label="Search everything"
          value={query}
          maxLength={200}
          onChange={(e) => setQuery(e.target.value)}
        />
      </form>
      <span className="tag hidden md:inline-flex" title="lore only reads the .md files; no route changes documentation">
        <Lock size={11} aria-hidden /> read-only
      </span>
    </header>
  );
}
