import cytoscape from "cytoscape";
import type { Core, ElementDefinition, StylesheetJson } from "cytoscape";
import fcose from "cytoscape-fcose";
import { Maximize2, Minus, Plus, RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";

import { shortLabel } from "../logic/graph.ts";
import type { Visible } from "../logic/graph.ts";
import type { Graph } from "../types.ts";

cytoscape.use(fcose);

// Three content colors (checked for color-blind safety, light and dark themes); everything else is
// told apart by SHAPE, label and legend — never by color alone.
export const COLORS = {
  light: { document: "#2a78d6", decision: "#eb6834", entity: "#1baf7a" },
  dark: { document: "#3987e5", decision: "#d95926", entity: "#199e70" },
};

export const SHAPES: Record<string, string> = {
  root: "round-rectangle",
  category: "round-rectangle",
  document: "ellipse",
  decision: "diamond",
  commit: "hexagon",
  migration: "barrel",
  hash: "round-hexagon",
  file: "tag",
};

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function stylesheet(): StylesheetJson {
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const color = dark ? COLORS.dark : COLORS.light;
  const fg = cssVar("--color-fg", "#18212b");
  const fg2 = cssVar("--color-fg-2", "#344354");
  const fg3 = cssVar("--color-fg-3", "#5b6b7d");
  const surface = cssVar("--color-surface", "#ffffff");
  const surface2 = cssVar("--color-surface-2", "#edf1f4");
  const line = cssVar("--color-line-strong", "#bcc6d2");
  const brand = cssVar("--color-brand", "#24407a");
  const warn = cssVar("--color-warn", "#8a5a00");
  const highlight = cssVar("--color-highlight", "#fff3b0");
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-family": "IBM Plex Sans, sans-serif",
        "font-size": 10,
        color: fg2,
        "text-valign": "bottom",
        "text-margin-y": 3,
        "min-zoomed-font-size": 7,
        "text-background-color": surface,
        "text-background-opacity": 0.75,
        "text-background-padding": "1px",
        width: "mapData(degree, 0, 40, 12, 34)",
        height: "mapData(degree, 0, 40, 12, 34)",
        "border-width": 2,
        "border-color": surface,
        "background-color": color.document,
        shape: "ellipse",
      },
    },
    { selector: "node[type = 'decision']", style: { "background-color": color.decision, shape: "diamond" } },
    { selector: "node[type = 'decision'][!document]", style: { "background-opacity": 0, "border-color": color.decision, "border-width": 2 } },
    { selector: "node[type = 'commit']", style: { "background-color": color.entity, shape: "hexagon" } },
    { selector: "node[type = 'migration']", style: { "background-color": color.entity, shape: "barrel" } },
    { selector: "node[type = 'hash']", style: { "background-color": color.entity, shape: "round-hexagon" } },
    { selector: "node[type = 'file']", style: { "background-color": color.entity, shape: "tag" } },
    {
      selector: "node[type = 'category']",
      style: {
        shape: "round-rectangle",
        "background-color": surface2,
        "border-color": fg2,
        "border-width": 1.5,
        width: "label",
        height: 20,
        padding: "6px",
        "text-valign": "center",
        "text-margin-y": 0,
        "font-size": 11,
        "font-weight": 600,
        color: fg,
        "text-background-opacity": 0,
      },
    },
    {
      selector: "node[type = 'root']",
      style: {
        shape: "round-rectangle",
        "background-color": fg,
        color: surface,
        width: "label",
        height: 26,
        padding: "8px",
        "text-valign": "center",
        "text-margin-y": 0,
        "font-size": 13,
        "font-weight": 600,
        "text-background-opacity": 0,
      },
    },
    { selector: "node.selected", style: { "border-color": brand, "border-width": 4, "z-index": 20 } },
    { selector: "node.found", style: { "overlay-color": highlight, "overlay-opacity": 0.55, "overlay-padding": 6 } },
    { selector: "node.expanded", style: { "border-style": "double", "border-width": 5, "border-color": brand } },
    {
      selector: "edge",
      style: {
        width: 1,
        "line-color": line,
        "curve-style": "straight",
        "target-arrow-shape": "triangle",
        "target-arrow-color": line,
        "arrow-scale": 0.6,
        opacity: 0.8,
      },
    },
    { selector: "edge[type = 'mentions']", style: { "line-style": "dotted", width: 1.2 } },
    { selector: "edge[type = 'subcategory']", style: { width: 2, "line-color": fg3, "target-arrow-shape": "none" } },
    { selector: "edge[type = 'category']", style: { "line-color": brand, "target-arrow-color": brand, opacity: 0.45 } },
    {
      selector: "edge[type = 'category'][confidence = 'suggestion']",
      style: { "line-style": "dashed", "line-dash-pattern": [4, 3], "line-color": warn, "target-arrow-color": warn, opacity: 0.6 },
    },
    { selector: "edge.selected", style: { width: 3, "line-color": brand, "target-arrow-color": brand, opacity: 1, "z-index": 20 } },
    { selector: ".hidden", style: { display: "none" } },
    { selector: ".dimmed", style: { opacity: 0.12 } },
  ] as StylesheetJson;
}


export interface GraphCanvasProps {
  graph: Graph;
  visible: Visible;
  selected: string | null;
  selectedEdge: string | null;
  found: Set<string>;
  expanded: Set<string>;
  layoutVersion: number;
  centerOn: { id: string; at: number } | null;
  onNode: (id: string) => void;
  onEdge: (id: string) => void;
  onExpand: (id: string) => void;
  onBackground: () => void;
}

export function GraphCanvas(props: GraphCanvasProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const cy = useRef<Core | null>(null);
  const handlers = useRef(props);
  handlers.current = props;

  // Cytoscape is created once per graph; filters only toggle classes.
  useEffect(() => {
    if (!container.current) return;
    const elements: ElementDefinition[] = [
      ...props.graph.nodes.map((n) => ({
        group: "nodes" as const,
        data: { id: n.id, type: n.type, label: shortLabel(n), degree: n.degree, document: n.document_id ?? undefined },
      })),
      ...props.graph.edges.map((e) => ({
        group: "edges" as const,
        data: { id: e.id, source: e.from, target: e.to, type: e.type, confidence: e.confidence ?? "explicit" },
      })),
    ];
    const instance = cytoscape({
      container: container.current,
      elements,
      style: stylesheet(),
      layout: { name: "preset" },
      minZoom: 0.08,
      maxZoom: 3,
      wheelSensitivity: 0.25,
      boxSelectionEnabled: false,
    });
    instance.on("tap", "node", (e) => handlers.current.onNode(e.target.id()));
    instance.on("tap", "edge", (e) => handlers.current.onEdge(e.target.id()));
    instance.on("dbltap", "node", (e) => handlers.current.onExpand(e.target.id()));
    instance.on("tap", (e) => {
      if (e.target === instance) handlers.current.onBackground();
    });
    cy.current = instance;
    // The container can change size after creation (responsive layout, side panel):
    // without this Cytoscape draws on a zero-height canvas.
    const observer = new ResizeObserver(() => {
      instance.resize();
      const shown = instance.elements().not(".hidden");
      if (shown.length) instance.fit(shown, 30);
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      instance.destroy();
      cy.current = null;
    };
  }, [props.graph]);

  // Visibility.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.batch(() => {
      c.nodes().forEach((n) => {
        n.toggleClass("hidden", !props.visible.nodes.has(n.id()));
      });
      c.edges().forEach((e) => {
        e.toggleClass("hidden", !props.visible.edges.has(e.id()));
      });
    });
  }, [props.visible]);

  // Re-lays out what is visible when the set really changes (filter, focus, expansion).
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    const shown = c.elements().not(".hidden");
    if (shown.length === 0) return;
    shown
      .layout({
        name: "fcose",
        animate: false,
        randomize: true,
        quality: "default",
        nodeRepulsion: () => 9000,
        idealEdgeLength: () => 70,
        nodeSeparation: 60,
        packComponents: true,
      } as cytoscape.LayoutOptions)
      .run();
    c.fit(shown, 30);
  }, [props.layoutVersion]);

  // Selection, search matches, expansions and neighborhood highlight.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.batch(() => {
      c.elements().removeClass("selected dimmed found expanded");
      props.found.forEach((id) => c.getElementById(id).addClass("found"));
      props.expanded.forEach((id) => c.getElementById(id).addClass("expanded"));
      if (props.selected) {
        const node = c.getElementById(props.selected);
        if (node.nonempty()) {
          node.addClass("selected");
          const neighborhood = node.closedNeighborhood();
          c.elements().not(".hidden").difference(neighborhood).addClass("dimmed");
        }
      }
      if (props.selectedEdge) c.getElementById(props.selectedEdge).addClass("selected");
    });
  }, [props.selected, props.selectedEdge, props.found, props.expanded, props.visible]);

  // Centers the requested node (search, selection from the panel).
  useEffect(() => {
    const c = cy.current;
    if (!c || !props.centerOn) return;
    const node = c.getElementById(props.centerOn.id);
    if (node.nonempty() && !node.hasClass("hidden")) {
      c.animate({ center: { eles: node }, zoom: Math.max(c.zoom(), 1.1) }, { duration: 250 });
    }
  }, [props.centerOn]);

  const zoom = (factor: number) => {
    const c = cy.current;
    if (!c) return;
    c.zoom({ level: c.zoom() * factor, renderedPosition: { x: c.width() / 2, y: c.height() / 2 } });
  };

  return (
    <div className="relative h-full w-full">
      {/* Cytoscape forces position: relative on the container; the height must come from h-full. */}
      <div ref={container} className="h-full w-full" role="img" aria-label="lore knowledge graph" />
      <div className="absolute top-2 right-2 flex flex-col gap-1">
        <button type="button" className="btn h-7 px-1.5" title="Zoom in" aria-label="Zoom in" onClick={() => zoom(1.25)}>
          <Plus size={14} aria-hidden />
        </button>
        <button type="button" className="btn h-7 px-1.5" title="Zoom out" aria-label="Zoom out" onClick={() => zoom(0.8)}>
          <Minus size={14} aria-hidden />
        </button>
        <button
          type="button"
          className="btn h-7 px-1.5"
          title="Fit all"
          aria-label="Fit all"
          onClick={() => cy.current?.fit(cy.current.elements().not(".hidden"), 30)}
        >
          <Maximize2 size={14} aria-hidden />
        </button>
        <button
          type="button"
          className="btn h-7 px-1.5"
          title="Re-layout"
          aria-label="Re-layout"
          onClick={() => {
            const c = cy.current;
            if (!c) return;
            const shown = c.elements().not(".hidden");
            shown.layout({ name: "fcose", animate: false, randomize: true, packComponents: true } as cytoscape.LayoutOptions).run();
            c.fit(shown, 30);
          }}
        >
          <RefreshCw size={14} aria-hidden />
        </button>
      </div>
    </div>
  );
}
