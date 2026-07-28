#!/usr/bin/env python3
"""Constellation relationship graph projection server.

Serves a REST API that projects canonical vault records into
React-Flow-compatible node/edge JSON. Read-only. No mutation.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# -- config ----------------------------------------------------------
VAULT = Path(os.environ.get(
    "CONSTELLATION_VAULT",
    os.path.expanduser("~/.hermes/profiles/cso/constellation"),
)).expanduser().resolve()
PORT = int(os.environ.get("GRAPH_PORT", "3457"))
ALLOW_ORIGIN = os.environ.get("GRAPH_CORS", "*")

# -- graph projection ------------------------------------------------

def graph_projection(
    vault: Path,
    *,
    focus_entity_id: str | None = None,
    max_hops: int = 2,
    node_types: set[str] | None = None,
    min_confidence: float = 0.0,
) -> dict:
    """Return {nodes, edges} from canonical vault records."""
    from constellation.graph import _relationship_records

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    # -- nodes from entities and people --
    for folder in ("entities", "people"):
        folder_path = vault / folder
        if not folder_path.is_dir():
            continue
        for md_path in sorted(folder_path.glob("*.md")):
            if md_path.is_symlink():
                continue
            try:
                from constellation.frontmatter import parse_frontmatter
                fm, body = parse_frontmatter(md_path.read_text())
                if not isinstance(fm, dict):
                    continue
                nid = str(fm.get("id", md_path.stem))
                ntype = str(fm.get("type", folder))
                if node_types and ntype not in node_types:
                    continue
                title = str(fm.get("title", md_path.stem))
                nodes[nid] = {
                    "id": nid,
                    "type": ntype,
                    "data": {
                        "label": title[:80],
                        "sensitivity": str(fm.get("sensitivity", "internal")),
                        "status": str(fm.get("status", "")),
                        "path": f"{folder}/{md_path.name}",
                    },
                    "position": {"x": 0, "y": 0},
                }
            except Exception:
                continue

    # -- edges from relationships --
    try:
        records = _relationship_records(vault)
    except Exception:
        records = []
    for rec in records:
        if min_confidence > 0 and getattr(rec, "confidence", 0) < min_confidence:
            continue
        sid = rec.subject_id
        oid = rec.object_id
        # add nodes if not already present (shouldn't happen with canonical records)
        for nid in (sid, oid):
            if nid not in nodes:
                nodes[nid] = {
                    "id": nid,
                    "type": "unknown",
                    "data": {"label": nid[:12], "sensitivity": "internal", "status": "", "path": ""},
                    "position": {"x": 0, "y": 0},
                }
        edges.append({
            "id": rec.id,
            "source": sid,
            "target": oid,
            "type": "smoothstep",
            "data": {
                "label": rec.predicate,
                "predicate": rec.predicate,
                "evidence_class": rec.evidence_class,
                "confidence": getattr(rec, "confidence", None),
                "sensitivity": rec.sensitivity.value,
                "source_ids": rec.source_ids,
            },
        })

    # -- if focus_entity_id, filter to bounded neighborhood --
    if focus_entity_id and focus_entity_id in nodes:
        reachable: set[str] = {focus_entity_id}
        frontier = {focus_entity_id}
        for _ in range(max_hops):
            next_frontier: set[str] = set()
            for e in edges:
                if e["source"] in frontier and e["target"] not in reachable:
                    next_frontier.add(e["target"])
                if e["target"] in frontier and e["source"] not in reachable:
                    next_frontier.add(e["source"])
            reachable |= next_frontier
            frontier = next_frontier
        nodes = {k: v for k, v in nodes.items() if k in reachable}
        edges = [e for e in edges if e["source"] in reachable and e["target"] in reachable]

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
    }


# -- HTTP server -----------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Constellation Graph</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#0d1117; color:#c9d1d9; }
  #root { width:100vw; height:100vh; }
  .controls { position:absolute; top:12px; left:12px; z-index:10; display:flex; gap:8px; flex-wrap:wrap; }
  .controls input, .controls select, .controls button { padding:6px 10px; border:1px solid #30363d; border-radius:6px; background:#161b22; color:#c9d1d9; font-size:13px; }
  .controls button { cursor:pointer; background:#238636; border-color:#238636; }
  .controls button:hover { background:#2ea043; }
  .detail-panel { position:absolute; top:60px; right:12px; z-index:10; background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; max-width:340px; max-height:80vh; overflow-y:auto; font-size:13px; display:none; }
  .detail-panel.show { display:block; }
  .detail-panel h3 { margin-bottom:8px; font-size:15px; }
  .detail-panel .row { margin-bottom:4px; }
  .detail-panel .label { color:#8b949e; }
  .detail-panel button { margin-top:8px; padding:4px 10px; border:1px solid #30363d; border-radius:4px; background:#21262d; color:#c9d1d9; cursor:pointer; font-size:12px; }
</style>
</head>
<body>
<div id="root"></div>
<div class="controls">
  <input id="entity-filter" placeholder="Entity ID (optional)" style="width:220px;">
  <select id="node-type-filter"><option value="">All types</option></select>
  <button onclick="loadGraph()">Load</button>
  <span id="status" style="color:#8b949e;align-self:center;font-size:12px;"></span>
</div>
<div class="detail-panel" id="detail"></div>

<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@xyflow/react@12/dist/xyflow-react.umd.min.js"></script>
<script>
const { ReactFlow, Background, Controls, MiniMap, useNodesState, useEdgesState, Handle, Position } = window.ReactFlow || {};
if (!ReactFlow) { document.getElementById('root').innerHTML = '<div style="padding:40px">ReactFlow failed to load. Check CDN.</div>'; }

const CustomNode = ({ data }) => React.createElement('div', {
  style: {
    padding: '8px 14px', borderRadius: 8,
    border: '1px solid ' + (data.sensitivity === 'restricted' ? '#da3633' : data.sensitivity === 'confidential' ? '#d29922' : '#30363d'),
    background: '#161b22', color: '#c9d1d9', fontSize: 13, maxWidth: 200, cursor: 'pointer',
  },
  onClick: () => window.showDetail('node', data),
}, [
  React.createElement('div', {style:{fontWeight:600}}, data.label),
  data.status ? React.createElement('div', {style:{fontSize:11,color:'#8b949e'}}, data.status) : null,
  React.createElement(Handle, {type:'target', position: Position.Top, style:{background:'#555'}}, null),
  React.createElement(Handle, {type:'source', position: Position.Bottom, style:{background:'#555'}}, null),
]);

const nodeTypes = { entity: CustomNode, person: CustomNode, company: CustomNode, organization: CustomNode, default: CustomNode };

const GraphView = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  window.loadGraph = async () => {
    document.getElementById('status').textContent = 'Loading...';
    const entityId = document.getElementById('entity-filter').value.trim();
    const params = new URLSearchParams();
    if (entityId) params.set('focus', entityId);
    try {
      const resp = await fetch('/api/graph?' + params.toString());
      const data = await resp.json();
      // layout nodes in a grid
      const n = data.nodes || [];
      const cols = Math.ceil(Math.sqrt(n.length));
      const positioned = n.map((node, i) => ({
        ...node,
        position: { x: (i % cols) * 250 + 50, y: Math.floor(i / cols) * 120 + 50 },
      }));
      setNodes(positioned);
      setEdges((data.edges || []).map(e => ({
        ...e,
        animated: false,
        style: { stroke: e.data?.sensitivity === 'restricted' ? '#da3633' : e.data?.sensitivity === 'confidential' ? '#d29922' : '#58a6ff' },
        label: e.data?.label || '',
        labelStyle: { fill: '#8b949e', fontSize: 10 },
        labelBgStyle: { fill: '#161b22' },
      })));
      document.getElementById('status').textContent = `${n.length} nodes, ${data.edges?.length || 0} edges`;
      // populate type filter
      const types = [...new Set(n.map(n => n.type))].sort();
      const sel = document.getElementById('node-type-filter');
      sel.innerHTML = '<option value="">All types</option>' + types.map(t => `<option value="${t}">${t}</option>`).join('');
    } catch (e) {
      document.getElementById('status').textContent = 'Error: ' + e.message;
    }
  };

  return React.createElement(ReactFlow, {
    nodes, edges, onNodesChange, onEdgesChange, nodeTypes,
    fitView: true, style: { background: '#0d1117' },
    onNodeClick: (evt, node) => window.showDetail('node', node.data),
    onEdgeClick: (evt, edge) => window.showDetail('edge', edge.data),
  },
    React.createElement(Background, {color:'#21262d', gap:20}),
    React.createElement(Controls, null),
    React.createElement(MiniMap, {style:{background:'#161b22'}, maskColor:'rgba(13,17,23,0.7)'}),
  );
};

window.showDetail = (kind, data) => {
  const panel = document.getElementById('detail');
  if (kind === 'node') {
    panel.innerHTML = `<h3>${data.label}</h3>
      <div class="row"><span class="label">Type:</span> ${data.type}</div>
      <div class="row"><span class="label">Status:</span> ${data.status || '—'}</div>
      <div class="row"><span class="label">Sensitivity:</span> ${data.sensitivity}</div>
      <div class="row"><span class="label">Path:</span> ${data.path}</div>
      <button onclick="document.getElementById('entity-filter').value='${data.id}';loadGraph();">Focus this entity</button>
      <button onclick="document.getElementById('detail').classList.remove('show')">Close</button>`;
  } else {
    panel.innerHTML = `<h3>${data.label || data.predicate}</h3>
      <div class="row"><span class="label">Evidence class:</span> ${data.evidence_class || '—'}</div>
      <div class="row"><span class="label">Confidence:</span> ${data.confidence ?? '—'}</div>
      <div class="row"><span class="label">Sensitivity:</span> ${data.sensitivity}</div>
      <div class="row"><span class="label">Source IDs:</span> ${(data.source_ids || []).join(', ') || '—'}</div>
      <button onclick="document.getElementById('detail').classList.remove('show')">Close</button>`;
  }
  panel.classList.add('show');
};

document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('root');
  if (ReactFlow && React && ReactDOM) {
    ReactDOM.createRoot(root).render(React.createElement(GraphView));
    loadGraph();
  }
});
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/graph":
            qs = parse_qs(parsed.query)
            focus = qs.get("focus", [None])[0]
            min_conf = float(qs.get("min_confidence", ["0"])[0])
            try:
                result = graph_projection(VAULT, focus_entity_id=focus, min_confidence=min_conf)
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        elif parsed.path in ("/", "/index.html"):
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[graph] {args[0]}", file=sys.stderr)


def main():
    print(f"Constellation Graph Server", file=sys.stderr)
    print(f"  vault: {VAULT}", file=sys.stderr)
    print(f"  port:  {PORT}", file=sys.stderr)
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
