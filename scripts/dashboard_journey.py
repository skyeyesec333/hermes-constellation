#!/usr/bin/env python3
"""Wave 5.3 dashboard journey — loopback probe of the private plugin endpoints.

Mounts the real Hermes dashboard plugin
(~/.hermes/plugins/constellation/dashboard/plugin_api.py) on a loopback
FastAPI instance and drives the operator journey over HTTP, read-only:

  projection -> neighbors -> path -> review/candidates -> review/watch-status
  -> static assets (dist/index.js, dist/style.css per manifest.json)

Every endpoint is read-only against the CSO vault by design; promotion stays
CLI-gated and is deliberately NOT probed here.

Run with a python that has fastapi/uvicorn/httpx (the dashboard host env):

  python3 scripts/dashboard_journey.py [--plugin-dir DIR]

The plugin dir defaults to $CONSTELLATION_DASHBOARD_PLUGIN_DIR or the
standard Hermes plugin location (~/.hermes/plugins/constellation/dashboard).
Exit codes: 0 = all probes pass, 1 = a probe failed, 2 = plugin not
installed (callers may treat 2 as skip).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

DEFAULT_PLUGIN_DIR = (
    Path.home() / ".hermes" / "plugins" / "constellation" / "dashboard"
)
API_PREFIX = "/api/plugins/constellation"
ASSET_PREFIX = "/plugins/constellation"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _serve(app, port: int):
    import uvicorn

    config = uvicorn.Config(app, host="localhost", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start within 15s")
        time.sleep(0.05)
    return server, thread


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        default=Path(os.environ.get("CONSTELLATION_DASHBOARD_PLUGIN_DIR", DEFAULT_PLUGIN_DIR)),
    )
    args = parser.parse_args()
    plugin_dir = args.plugin_dir

    if not (plugin_dir / "plugin_api.py").is_file():
        print(json.dumps({"ok": None, "skipped": f"plugin not installed at {plugin_dir}"}))
        return 2

    try:
        import httpx
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - env guard
        print(f"FAIL: dashboard host deps missing: {exc}")
        return 1

    sys.path.insert(0, str(plugin_dir))
    import plugin_api  # noqa: E402

    app = FastAPI()
    app.include_router(plugin_api.router, prefix=API_PREFIX)
    app.mount(ASSET_PREFIX, StaticFiles(directory=plugin_dir), name="plugin-assets")

    port = _free_port()
    server, _thread = _serve(app, port)
    base = f"http://localhost:{port}"

    results: list[dict] = []

    def step(name: str, fn) -> None:
        try:
            detail = fn()
            results.append({"step": name, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - evidence harness
            results.append({"step": name, "ok": False, "error": str(exc)})

    node_ids: list[str] = []

    def probe_projection():
        r = httpx.get(f"{base}{API_PREFIX}/projection", timeout=30)
        r.raise_for_status()
        data = r.json()
        nodes = data.get("nodes", [])
        assert nodes, "projection returned zero nodes"
        node_ids.extend(n["id"] for n in nodes)
        return {"nodes": len(nodes), "edges": len(data.get("edges", []))}

    def probe_neighbors():
        r = httpx.get(f"{base}{API_PREFIX}/neighbors/{node_ids[0]}", timeout=30)
        r.raise_for_status()
        return {"node": node_ids[0], "keys": sorted(r.json().keys())}

    def probe_path():
        start, end = node_ids[0], node_ids[min(1, len(node_ids) - 1)]
        r = httpx.get(
            f"{base}{API_PREFIX}/path", params={"start": start, "end": end}, timeout=30
        )
        r.raise_for_status()
        return {"start": start, "end": end}

    def probe_timeline():
        r = httpx.get(f"{base}{API_PREFIX}/timeline/{node_ids[0]}", timeout=30)
        r.raise_for_status()
        data = r.json()
        assert "entries" in data, "timeline missing entries key"
        return {"node": node_ids[0], "entries": len(data["entries"])}

    def probe_briefing():
        r = httpx.get(f"{base}{API_PREFIX}/briefing/{node_ids[0]}", timeout=30)
        r.raise_for_status()
        data = r.json()
        assert "claims" in data and "candidates" in data, "briefing missing model keys"
        return {"node": node_ids[0], "claims": len(data["claims"])}

    def probe_review_candidates():
        r = httpx.get(f"{base}{API_PREFIX}/review/candidates", timeout=30)
        r.raise_for_status()
        return {"total": r.json().get("total")}

    def probe_watch_status():
        r = httpx.get(f"{base}{API_PREFIX}/review/watch-status", timeout=30)
        r.raise_for_status()
        return {"keys": sorted(r.json().keys())}

    def probe_assets():
        for asset in ("dist/index.js", "dist/style.css"):
            r = httpx.get(f"{base}{ASSET_PREFIX}/{asset}", timeout=30)
            r.raise_for_status()
            assert len(r.content) > 0, f"{asset} served empty"
        return {"assets": ["dist/index.js", "dist/style.css"]}

    def probe_sna():
        r = httpx.get(f"{base}{API_PREFIX}/sna", timeout=60)
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok" and "top_nodes" in data, "sna missing model keys"
        return {"nodes": data.get("node_count")}

    def probe_communities():
        r = httpx.get(f"{base}{API_PREFIX}/communities", timeout=30)
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok" and "components" in data, "communities missing keys"
        return {"components": data.get("component_count")}

    def probe_typologies():
        r = httpx.get(f"{base}{API_PREFIX}/typologies", timeout=30)
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok" and "matches" in data, "typologies missing keys"
        return {"matches": len(data.get("matches", []))}

    def probe_hypotheses():
        r = httpx.get(f"{base}{API_PREFIX}/hypotheses", timeout=30)
        r.raise_for_status()
        assert isinstance(r.json(), list), "hypotheses must return a list"
        return {"packets": len(r.json())}

    def probe_graph_delta():
        r = httpx.post(f"{base}{API_PREFIX}/graph-delta/snapshot", timeout=60)
        r.raise_for_status()
        snap_path = r.json().get("snapshot_path")
        assert snap_path, "snapshot missing path"
        r = httpx.get(
            f"{base}{API_PREFIX}/graph-delta/diff",
            params={"from_snapshot": snap_path, "to_snapshot": snap_path},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        assert data.get("status") == "ok" and data["totals"]["added"] == 0, "self-diff must be empty"
        return {"snapshot": snap_path, "unchanged": data["totals"]["unchanged"]}

    try:
        step("projection", probe_projection)
        if node_ids:
            step("neighbors", probe_neighbors)
            step("path", probe_path)
            step("timeline", probe_timeline)
            step("briefing", probe_briefing)
        else:
            for missing in ("neighbors", "path", "timeline", "briefing"):
                results.append({"step": missing, "ok": False, "error": "no nodes from projection"})
        step("review_candidates", probe_review_candidates)
        step("review_watch_status", probe_watch_status)
        step("static_assets", probe_assets)
        step("sna", probe_sna)
        step("communities", probe_communities)
        step("typologies", probe_typologies)
        step("hypotheses", probe_hypotheses)
        step("graph_delta", probe_graph_delta)
    finally:
        server.should_exit = True

    ok = all(r["ok"] for r in results)
    print(json.dumps({"ok": ok, "base": base, "steps": results}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
