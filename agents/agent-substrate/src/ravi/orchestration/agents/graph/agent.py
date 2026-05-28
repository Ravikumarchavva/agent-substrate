"""FlowGraph — lightweight static representation of a multi-agent flow.

A FlowGraph is a description of the topology (nodes + directed edges);
it does not execute anything. Flows build a graph via to_graph() so that:

* Frontends can render a live visualization (e.g. ReactFlow).
* The GET /flows/{name}/graph API endpoint can return serializable JSON.
* Mermaid diagrams can be embedded in docs or Markdown reports.
* Jupyter notebooks can render an interactive visualization via draw().
"""

from __future__ import annotations

import base64
import json
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

NodeType = Literal["agent", "flow", "condition", "input", "output"]

_THEMES = {
    "dark": {
        "bg": "#0f0f1a",
        "card_bg": "#16213e",
        "text": "#e2e8f0",
        "border": "#4a5568",
        "mermaid_theme": "dark",
    },
    "light": {
        "bg": "#f8fafc",
        "card_bg": "#ffffff",
        "text": "#1e293b",
        "border": "#e2e8f0",
        "mermaid_theme": "default",
    },
    "forest": {
        "bg": "#0d1117",
        "card_bg": "#161b22",
        "text": "#c9d1d9",
        "border": "#30363d",
        "mermaid_theme": "forest",
    },
}


@dataclass
class FlowNode:
    """A single node in the flow topology."""

    id: str
    label: str
    node_type: NodeType = "agent"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type,
            "metadata": self.metadata,
        }


@dataclass
class FlowEdge:
    """A directed edge between two FlowNode instances."""

    source: str
    target: str
    label: Optional[str] = None
    condition: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"source": self.source, "target": self.target}
        if self.label:
            d["label"] = self.label
        if self.condition:
            d["condition"] = self.condition
        return d


@dataclass
class FlowGraph:
    """Complete directed graph describing a flow topology.

    Usage::

        graph = my_flow.to_graph()
        graph.draw()                   # inline HTML in Jupyter
        graph.draw(output="png")       # static PNG via mermaid.ink
        graph.draw(output="mermaid")   # print raw Mermaid text
        payload = graph.to_dict()      # JSON for REST API
    """

    nodes: List[FlowNode] = field(default_factory=list)
    edges: List[FlowEdge] = field(default_factory=list)
    name: Optional[str] = None

    def add_node(self, node: FlowNode) -> "FlowGraph":
        self.nodes.append(node)
        return self

    def add_edge(self, edge: FlowEdge) -> "FlowGraph":
        self.edges.append(edge)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    def to_mermaid(self) -> str:
        """Render as a Mermaid flowchart LR diagram."""
        # node_type -> (open_delim, close_delim) for Mermaid node shapes
        _SHAPE = {
            "input": ('(["', '"])'),  # stadium / rounded rect
            "output": ('(["', '"])'),  # stadium / rounded rect
            "condition": ("{", "}"),  # diamond / rhombus
            "flow": ('(["', '"])'),  # stadium
            "agent": ('["', '"]'),  # rectangle with quoted label
        }
        lines = ["flowchart LR"]
        for node in self.nodes:
            o, c = _SHAPE.get(node.node_type, ('["', '"]'))
            lines.append(f"  {node.id}{o}{node.label}{c}")
        for edge in self.edges:
            if edge.label:
                lines.append(f'  {edge.source} -->|"{edge.label}"| {edge.target}')
            else:
                lines.append(f"  {edge.source} --> {edge.target}")
        return "\n".join(lines)

    def draw(self, output: str = "html", theme: str = "dark") -> None:
        """Render the flow graph visually.

        Args:
            output: "html" (Mermaid.js inline, default), "png" (mermaid.ink API),
                    or "mermaid" (print raw text).
            theme:  "dark" (default), "light", or "forest".
        """
        mermaid_code = self.to_mermaid()
        if output == "mermaid":
            print(mermaid_code)
            return
        try:
            from importlib import import_module

            ipython_display = import_module("IPython.display")
            display = ipython_display.display
            HTML = ipython_display.HTML
            Image = ipython_display.Image
        except ImportError:
            print(mermaid_code)
            return
        if output == "png":
            self._display_png(mermaid_code, display, Image)
        else:
            self._display_html(mermaid_code, theme, display, HTML)

    def _display_png(self, mermaid_code: str, display: Any, Image: Any) -> None:
        try:
            payload = json.dumps(
                {"code": mermaid_code, "mermaid": {"theme": "default"}}
            )
            encoded = base64.urlsafe_b64encode(payload.encode()).decode()
            display(Image(url=f"https://mermaid.ink/img/{encoded}", width=900))
        except Exception as exc:
            print(f"[FlowGraph.draw] PNG render failed ({exc}). Mermaid fallback:")
            print(mermaid_code)

    def _display_html(
        self, mermaid_code: str, theme: str, display: Any, HTML: Any
    ) -> None:
        t = _THEMES.get(theme, _THEMES["dark"])
        uid = _uuid.uuid4().hex[:8]
        title = self.name or "Flow Graph"
        badge = f"{len(self.nodes)} nodes \u00b7 {len(self.edges)} edges"
        html = f"""
<div id="fg-{uid}" style="background:{t["bg"]};border:1px solid {t["border"]};border-radius:12px;padding:20px 24px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:8px 0;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid {t["border"]};">
    <span style="color:{t["text"]};font-weight:600;font-size:14px;">&#x229E; {title}</span>
    <div style="display:flex;gap:8px;align-items:center;">
      <button id="fg-zoom-in-{uid}" style="background:{t["card_bg"]};color:{t["text"]};border:1px solid {t["border"]};border-radius:4px;padding:2px 10px;cursor:pointer;font-size:16px;font-weight:600;line-height:1.6;">+</button>
      <button id="fg-zoom-out-{uid}" style="background:{t["card_bg"]};color:{t["text"]};border:1px solid {t["border"]};border-radius:4px;padding:2px 10px;cursor:pointer;font-size:16px;font-weight:600;line-height:1.6;">−</button>
      <span id="fg-zoom-label-{uid}" style="color:#718096;font-size:11px;width:36px;text-align:center;">100%</span>
      <span style="color:#718096;font-size:11px;margin-left:4px;">{badge}</span>
    </div>
  </div>
  <div id="fg-container-{uid}" style="overflow:auto;background:{t["card_bg"]};border-radius:8px;padding:24px;position:relative;min-height:300px;">
    <div id="fg-svg-wrapper-{uid}" style="transform-origin:top left;transition:transform 0.15s ease;display:inline-block;min-width:100%;">
      <pre id="fg-pre-{uid}" style="display:none;">{mermaid_code}</pre>
    </div>
  </div>
</div>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{
    startOnLoad: false,
    theme: '{t["mermaid_theme"]}',
    flowchart: {{curve:'basis', nodeSpacing:60, rankSpacing:80, padding:30, useMaxWidth:false}},
    securityLevel: 'loose'
  }});

  const wrapper = document.getElementById('fg-svg-wrapper-{uid}');
  const container = document.getElementById('fg-container-{uid}');
  const zoomInBtn = document.getElementById('fg-zoom-in-{uid}');
  const zoomOutBtn = document.getElementById('fg-zoom-out-{uid}');
  const zoomLabel = document.getElementById('fg-zoom-label-{uid}');
  const pre = document.getElementById('fg-pre-{uid}');

  let zoom = 1;

  const applyZoom = () => {{
    wrapper.style.transform = `scale(${{zoom}})`;
    wrapper.style.transformOrigin = 'top left';
    // grow the wrapper so the container scrollbar reflects true scaled size
    const svgEl = wrapper.querySelector('svg');
    if (svgEl) {{
      const naturalW = svgEl.viewBox.baseVal.width || svgEl.getBoundingClientRect().width / zoom;
      const naturalH = svgEl.viewBox.baseVal.height || svgEl.getBoundingClientRect().height / zoom;
      wrapper.style.width  = (naturalW * zoom) + 'px';
      wrapper.style.height = (naturalH * zoom) + 'px';
    }}
    zoomLabel.textContent = Math.round(zoom * 100) + '%';
  }};

  zoomInBtn.addEventListener('click', () => {{ zoom = Math.min(+(zoom + 0.1).toFixed(1), 3); applyZoom(); }});
  zoomOutBtn.addEventListener('click', () => {{ zoom = Math.max(+(zoom - 0.1).toFixed(1), 0.3); applyZoom(); }});

  if (pre) {{
    const code = pre.textContent.trim();
    const {{ svg }} = await mermaid.render('fg-svg-{uid}', code);
    wrapper.innerHTML = svg;

    const svgEl = wrapper.querySelector('svg');
    if (svgEl) {{
      // Strip Mermaid's hardcoded width/height attrs so CSS controls sizing
      svgEl.removeAttribute('width');
      svgEl.removeAttribute('height');
      svgEl.style.display = 'block';

      // Fit to container width on first render
      const containerW = container.clientWidth - 48;
      const vb = svgEl.viewBox.baseVal;
      if (vb && vb.width > 0) {{
        zoom = Math.min(containerW / vb.width, 1.5);
        zoom = Math.max(+(zoom).toFixed(2), 0.3);
        svgEl.setAttribute('width',  vb.width + 'px');
        svgEl.setAttribute('height', vb.height + 'px');
        applyZoom();
      }}
    }}
  }}
</script>"""
        display(HTML(html))

    def __repr__(self) -> str:
        return f"<FlowGraph(name={self.name!r}, nodes={len(self.nodes)}, edges={len(self.edges)})>"
