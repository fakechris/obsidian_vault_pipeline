"""
Visualization - 图谱可视化

支持:
- ASCII art (终端)
- HTML (浏览器打开)
- GraphML (Gephi等工具)
"""

from pathlib import Path
from typing import Optional
import html as _html
import json
import re


def _safe_json(payload: object) -> str:
    """JSON-in-HTML 安全序列化：把 </ 换成 <\\/，避免 "</script>" 字面值
    提前关闭 <script> 块（JS 字符串里 \\/ 和 / 等价）。"""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _html_escape(s: str) -> str:
    return _html.escape(s or '', quote=True)


_SAFE_CLASS_RE = re.compile(r'[^a-zA-Z0-9_-]')


def _safe_class(s: str) -> str:
    """把 note_type / edge_type 映射成 Cytoscape class 名（只保留字母数字/_/-）"""
    return _SAFE_CLASS_RE.sub('-', s or 'unknown') or 'unknown'


# note_type → (颜色, 形状)。颜色来自 Tailwind 调色板；形状区分概念 vs 源文档。
# Closed canonical set since Phase 38.D — anything not in this map renders with
# the fallback gray ellipse and is flagged by `ovp-lint --check-note-types`.
_TYPE_STYLE: dict[str, tuple[str, str]] = {
    'evergreen': ('#34d399', 'ellipse'),
    'moc': ('#f472b6', 'diamond'),
    'deep_dive': ('#818cf8', 'round-rectangle'),
    'raw': ('#94a3b8', 'round-rectangle'),
    'article': ('#fbbf24', 'round-rectangle'),
    'project': ('#f97316', 'round-rectangle'),
    'essay': ('#a78bfa', 'round-rectangle'),
    'daily_view': ('#fb923c', 'hexagon'),
}


def _render_type_filter(counts: list[tuple[str, int]]) -> str:
    rows = []
    for note_type, count in counts:
        color, _shape = _TYPE_STYLE.get(note_type, ('#888', 'ellipse'))
        rows.append(
            f'<label class="type-row"><input type="checkbox" checked '
            f'data-type="{_html_escape(note_type)}"> '
            f'<span class="swatch" style="background:{color}"></span>'
            f'{_html_escape(note_type)} '
            f'<span class="count">{count}</span></label>'
        )
    return '\n'.join(rows)


_CYTOSCAPE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{page_title}</title>
<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<!-- fcose (and its peers) is the primary layout; cose-bilkent is kept loaded as a graceful fallback. -->
<script src="https://unpkg.com/layout-base@2.0.1/layout-base.js"></script>
<script src="https://unpkg.com/cose-base@2.2.0/cose-base.js"></script>
<script src="https://unpkg.com/numeric@1.2.6/numeric-1.2.6.min.js"></script>
<script src="https://unpkg.com/cytoscape-fcose@2.2.0/cytoscape-fcose.js"></script>
<script src="https://unpkg.com/cytoscape-cose-bilkent@4.1.0/cytoscape-cose-bilkent.js"></script>
<!-- BubbleSets draws the soft cluster envelopes on a canvas overlay.
     Requires cytoscape-layers as a peer dep. -->
<script src="https://unpkg.com/cytoscape-layers@3.1.0/build/index.umd.min.js"></script>
<script src="https://unpkg.com/cytoscape-bubblesets@4.1.0/build/index.umd.min.js"></script>
<style>
  :root {{
    --bg: #0b0e17;
    --panel: #121826;
    --panel-border: #1f2a3d;
    --text: #e6edf3;
    --muted: #8b96a6;
    --accent: #00d4ff;
    --seed: #00d4ff;
    --hop1: #4ade80;
    --hop2: #fbbf24;
    --hop3: #f87171;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--text);
    font: 13px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  .app {{ display: grid; grid-template-columns: 260px 1fr 320px; height: 100vh; }}
  .sidebar, .detail {{ background: var(--panel); border-right: 1px solid var(--panel-border);
    overflow-y: auto; padding: 14px; }}
  .detail {{ border-right: none; border-left: 1px solid var(--panel-border); }}
  .sidebar h2, .detail h2 {{ margin: 0 0 4px 0; font-size: 14px; color: var(--accent); }}
  .sidebar h3 {{ margin: 16px 0 6px 0; font-size: 11px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.5px; }}
  .meta {{ color: var(--muted); font-size: 11px; margin-bottom: 6px; }}
  .stats {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 12px; }}
  .stat {{ background: #0f1524; border: 1px solid var(--panel-border);
    padding: 6px 10px; border-radius: 6px; min-width: 70px; }}
  .stat .v {{ font-size: 18px; font-weight: 600; color: var(--accent); }}
  .stat .l {{ font-size: 10px; color: var(--muted); text-transform: uppercase; }}
  input[type=text] {{ width: 100%; background: #0f1524; color: var(--text);
    border: 1px solid var(--panel-border); border-radius: 6px; padding: 6px 8px; font: inherit; }}
  .type-row {{ display: flex; align-items: center; gap: 6px; padding: 3px 2px; cursor: pointer;
    font-size: 12px; }}
  .type-row:hover {{ background: #0f1524; border-radius: 4px; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; }}
  .count {{ color: var(--muted); font-size: 11px; margin-left: auto; }}
  .btn-row {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  button {{ background: #0f1524; color: var(--text); border: 1px solid var(--panel-border);
    padding: 5px 10px; border-radius: 5px; cursor: pointer; font: inherit; }}
  button:hover {{ border-color: var(--accent); color: var(--accent); }}
  #cy {{ width: 100%; height: 100%; background: #070a12; }}
  .detail .empty {{ color: var(--muted); font-size: 12px; margin-top: 20px; }}
  .detail .field {{ margin: 8px 0; }}
  .detail .field .k {{ color: var(--muted); font-size: 11px; text-transform: uppercase; }}
  .detail .field .v {{ word-break: break-word; font-size: 12px; }}
  .detail .neighbor {{ padding: 3px 4px; border-radius: 4px; cursor: pointer; font-size: 12px;
    color: var(--text); display: block; text-decoration: none; }}
  .detail .neighbor:hover {{ background: #0f1524; color: var(--accent); }}
  .detail .neighbor .pill {{ display: inline-block; padding: 0 5px; margin-right: 5px;
    border-radius: 9px; font-size: 10px; background: #1f2a3d; color: var(--muted); }}
  .row-hop-label {{ font-size: 11px; color: var(--muted); margin-right: 6px; }}
  .legend-hop {{ display: flex; gap: 10px; font-size: 11px; margin-top: 8px; color: var(--muted); }}
  .legend-hop .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 3px; vertical-align: middle; }}
  /* BubbleSets paints into a <canvas> overlay that shares the #cy stacking context. */
  #cy {{ position: relative; }}
  .cluster-legend {{ font-size: 11px; color: var(--muted); margin-top: 6px; line-height: 1.6; }}
  .cluster-legend .pill {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px;
    margin-right: 5px; vertical-align: middle; opacity: 0.7; border: 1px solid rgba(255,255,255,0.1); }}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <h2>{page_title}</h2>
    <div class="meta">Generated: {generated_at}</div>
    <div class="stats">
      <div class="stat"><div class="v">{node_count}</div><div class="l">Nodes</div></div>
      <div class="stat"><div class="v">{edge_count}</div><div class="l">Edges</div></div>
      <div class="stat"><div class="v">{seed_count}</div><div class="l">Seeds</div></div>
    </div>

    <h3>Search</h3>
    <input type="text" id="search" placeholder="filter by title…">

    <h3>Filter by type</h3>
    <div id="type-filter">
{type_filter_html}
    </div>

    <h3>Filter by hop</h3>
    <label class="type-row"><input type="checkbox" checked data-hop="0">
      <span class="swatch" style="background:var(--seed)"></span>Seed (hop 0)</label>
    <label class="type-row"><input type="checkbox" checked data-hop="1">
      <span class="swatch" style="background:var(--hop1)"></span>Hop 1</label>
    <label class="type-row"><input type="checkbox" checked data-hop="2">
      <span class="swatch" style="background:var(--hop2)"></span>Hop 2</label>
    <label class="type-row"><input type="checkbox" checked data-hop="3">
      <span class="swatch" style="background:var(--hop3)"></span>Hop 3+</label>

    <h3>View</h3>
    <div class="btn-row">
      <button id="btn-fit">Fit</button>
      <button id="btn-seeds">Focus Seeds</button>
      <button id="btn-layout">Re-layout</button>
      <button id="btn-reset">Reset</button>
    </div>

    <h3>Hop2 collapse</h3>
    <div id="collapse-stat" class="meta">—</div>
    <div class="btn-row">
      <button id="btn-expand-all">Expand all hop2</button>
      <button id="btn-collapse-all">Collapse all hop2</button>
      <button id="btn-expand-selected">Expand connected to selected</button>
    </div>

    <h3>Clusters</h3>
    <label class="type-row"><input type="checkbox" id="cluster-toggle" checked>
      Show connected-component hulls</label>
    <div id="cluster-legend" class="cluster-legend"></div>
  </aside>

  <main id="cy"></main>

  <aside class="detail">
    <h2>Node</h2>
    <div id="detail-body" class="empty">点击任意节点查看详情。</div>
  </aside>
</div>

<script>
(function() {{
  var payload = {elements_json};
  var COLLAPSE_THRESHOLD = {collapse_threshold};
  var INITIAL_AUTO_COLLAPSE = {initial_auto_collapse_js};

  var TYPE_STYLE = {{
    'evergreen':          ['#34d399','ellipse'],
    'moc':                ['#f472b6','diamond'],
    'deep_dive':          ['#818cf8','round-rectangle'],
    'raw':                ['#94a3b8','round-rectangle'],
    'article':            ['#fbbf24','round-rectangle'],
    'technical-analysis': ['#06b6d4','round-rectangle'],
    'github-project':     ['#f97316','round-rectangle'],
    'project':            ['#a78bfa','round-rectangle'],
    'ai':                 ['#22d3ee','round-rectangle'],
    'daily_view':         ['#fb923c','hexagon'],
    'interpretation':     ['#fde047','round-rectangle']
  }};

  function typeStyle(t) {{ return TYPE_STYLE[t] || ['#888','ellipse']; }}

  var style = [
    {{ selector: 'node', style: {{
        'label': 'data(label)',
        'color': '#e6edf3',
        'font-size': 10,
        'text-wrap': 'wrap',
        'text-max-width': 140,
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'background-color': function(ele) {{ return typeStyle(ele.data('note_type'))[0]; }},
        'shape': function(ele) {{ return typeStyle(ele.data('note_type'))[1]; }},
        'border-color': '#1f2a3d',
        'border-width': 1,
        'width': 22, 'height': 22,
        'transition-property': 'opacity, border-width, border-color',
        'transition-duration': '120ms'
    }}}},
    {{ selector: 'node.role-seed',           style: {{ 'border-color':'#00d4ff','border-width':4,'width':40,'height':40,'font-size':12 }} }},
    {{ selector: 'node.role-neighbor_1hop',  style: {{ 'border-color':'#4ade80','border-width':2,'width':28,'height':28 }} }},
    {{ selector: 'node.role-neighbor_2hop',  style: {{ 'border-color':'#fbbf24','border-width':2,'width':22,'height':22 }} }},
    {{ selector: 'node.role-neighbor_3hop',  style: {{ 'border-color':'#f87171','border-width':1,'width':18,'height':18 }} }},
    {{ selector: 'edge', style: {{
        'width': 1,
        'line-color': '#263248',
        'target-arrow-color': '#263248',
        'target-arrow-shape': 'triangle',
        'curve-style': 'unbundled-bezier',
        'control-point-distances': [12],
        'control-point-weights': [0.5],
        'arrow-scale': 0.8,
        'opacity': 0.75,
        'transition-property': 'opacity, line-color, width',
        'transition-duration': '120ms'
    }}}},
    {{ selector: 'edge.edgetype-promoted_from', style: {{
        'line-color': '#a78bfa',
        'target-arrow-color': '#a78bfa',
        'line-style': 'dashed',
        'opacity': 0.6
    }}}},
    {{ selector: '.faded', style: {{ 'opacity': 0.08 }} }},
    {{ selector: '.highlighted', style: {{
        'border-color':'#00d4ff',
        'border-width': 3
    }}}},
    {{ selector: 'edge.highlighted', style: {{
        'line-color':'#00d4ff',
        'target-arrow-color':'#00d4ff',
        'width': 2,
        'opacity': 1
    }}}},
    {{ selector: '.hover-hl', style: {{
        'border-color':'#fde68a',
        'border-width': 3
    }}}},
    {{ selector: 'edge.hover-hl', style: {{
        'line-color':'#fde68a',
        'target-arrow-color':'#fde68a',
        'width': 2,
        'opacity': 0.95
    }}}},
    {{ selector: '.hidden', style: {{ 'display':'none' }} }},
    /* Collapsed hop2: keep the node in the layout but invisible & non-interactive,
       so animated collapse/expand can fade them in/out without re-running layout. */
    {{ selector: '.collapsed-hop2', style: {{
        'opacity': 0,
        'events': 'no',
        'text-opacity': 0
    }}}},
    {{ selector: 'edge.collapsed-hop2', style: {{
        'opacity': 0,
        'events': 'no'
    }}}}
  ];

  // Layout selection: fcose is the primary (faster, smoother edges, supports
  // cluster constraints); fall back to cose-bilkent if fcose's CDN script
  // failed to register so the page still renders. Some UMD builds don't
  // auto-register, so we call cytoscape.use defensively.
  (function _registerLayouts() {{
    try {{
      if (typeof window.cytoscapeFcose !== 'undefined' && cytoscape && typeof cytoscape.use === 'function') {{
        cytoscape.use(window.cytoscapeFcose);
      }}
    }} catch (e) {{ /* already registered */ }}
    try {{
      if (typeof window.cytoscapeCoseBilkent !== 'undefined' && cytoscape && typeof cytoscape.use === 'function') {{
        cytoscape.use(window.cytoscapeCoseBilkent);
      }}
    }} catch (e) {{ /* already registered */ }}
  }})();

  function preferredLayoutName() {{
    if (typeof window.cytoscapeFcose !== 'undefined') return 'fcose';
    if (typeof window.cytoscapeCoseBilkent !== 'undefined') return 'cose-bilkent';
    return 'cose';
  }}

  function layoutOptions(name) {{
    if (name === 'fcose') {{
      // randomize:true is critical — without preset positions all nodes
      // start at (0,0) and the layout collapses to a degenerate line.
      return {{
        name: 'fcose',
        animate: 'end',
        animationDuration: 600,
        quality: 'proof',
        randomize: true,
        nodeDimensionsIncludeLabels: true,
        uniformNodeDimensions: false,
        idealEdgeLength: 110,
        nodeRepulsion: 6500,
        edgeElasticity: 0.45,
        gravity: 0.3,
        gravityRange: 3.8,
        numIter: 2500,
        tile: true,
        tilingPaddingVertical: 12,
        tilingPaddingHorizontal: 12,
        packComponents: true,
        padding: 40,
        fit: true
      }};
    }}
    if (name === 'cose-bilkent') {{
      return {{
        name: 'cose-bilkent',
        animate: 'end',
        randomize: true,
        idealEdgeLength: 110,
        nodeRepulsion: 7000,
        nodeOverlap: 24,
        gravity: 0.3,
        numIter: 2000,
        tile: true,
        padding: 40
      }};
    }}
    return {{ name: 'cose', animate: false, padding: 40, fit: true }};
  }}

  var LAYOUT_NAME = preferredLayoutName();
  if (typeof console !== 'undefined') {{ console.log('OVP Graph layout:', LAYOUT_NAME); }}

  var cy = cytoscape({{
    container: document.getElementById('cy'),
    elements: payload,
    style: style,
    layout: layoutOptions(LAYOUT_NAME),
    wheelSensitivity: 0.2,
    minZoom: 0.1,
    maxZoom: 3
  }});

  // ---------- Detail panel ----------
  var detailEl = document.getElementById('detail-body');

  function renderDetail(node) {{
    if (!node) {{
      detailEl.className = 'empty';
      detailEl.textContent = '点击任意节点查看详情。';
      return;
    }}
    var d = node.data();
    var incoming = node.incomers('node');
    var outgoing = node.outgoers('node');
    var html = '';
    html += '<div class="field"><div class="k">title</div><div class="v"><strong>' + esc(d.title) + '</strong></div></div>';
    html += '<div class="field"><div class="k">type</div><div class="v">' + esc(d.note_type) + '</div></div>';
    if (d.distance >= 0) {{
      var label = d.distance === 0 ? 'seed' : 'hop ' + d.distance;
      html += '<div class="field"><div class="k">distance</div><div class="v">' + label + '</div></div>';
    }}
    if (d.path) {{
      html += '<div class="field"><div class="k">path</div><div class="v" style="font-family:ui-monospace,Menlo,monospace;font-size:11px;">' + esc(d.path) + '</div></div>';
    }}
    if (d.day_id) {{
      html += '<div class="field"><div class="k">day</div><div class="v">' + esc(d.day_id) + '</div></div>';
    }}
    html += '<div class="field"><div class="k">incoming (' + incoming.length + ')</div><div class="v">' + neighborList(incoming) + '</div></div>';
    html += '<div class="field"><div class="k">outgoing (' + outgoing.length + ')</div><div class="v">' + neighborList(outgoing) + '</div></div>';
    detailEl.className = '';
    detailEl.innerHTML = html;
    detailEl.querySelectorAll('.neighbor').forEach(function(a) {{
      a.addEventListener('click', function(e) {{
        e.preventDefault();
        var target = cy.getElementById(a.dataset.id);
        if (target.nonempty()) {{
          selectNode(target);
          cy.center(target);
        }}
      }});
    }});
  }}

  function neighborList(collection) {{
    if (!collection.length) return '<span style="color:var(--muted)">(none)</span>';
    var items = [];
    collection.forEach(function(n) {{
      var nd = n.data();
      items.push('<a class="neighbor" href="#" data-id="' + esc(nd.id) + '">'
        + '<span class="pill">' + esc(nd.note_type) + '</span>' + esc(nd.title) + '</a>');
    }});
    return items.join('');
  }}

  function esc(s) {{
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }}

  // ---------- Selection / highlight ----------
  var currentSelection = null;

  function selectNode(node) {{
    currentSelection = node;
    cy.elements().removeClass('highlighted faded');
    var nbrs = node.closedNeighborhood();
    cy.elements().not(nbrs).addClass('faded');
    nbrs.addClass('highlighted');
    renderDetail(node);
  }}

  function clearSelection() {{
    currentSelection = null;
    cy.elements().removeClass('highlighted faded');
    renderDetail(null);
  }}

  cy.on('tap', 'node', function(evt) {{ selectNode(evt.target); }});
  cy.on('tap', function(evt) {{ if (evt.target === cy) clearSelection(); }});

  // ---------- Hover-highlight (transient; doesn't stomp on click selection) ----------
  // Mouseover paints a soft amber glow on the node + its incident edges.
  // We never clear the persistent click-highlight from `selectNode`, so the
  // two states layer cleanly: amber = ephemeral, cyan = sticky.
  cy.on('mouseover', 'node', function(evt) {{
    var n = evt.target;
    if (n.hasClass('collapsed-hop2')) return;
    n.addClass('hover-hl');
    n.connectedEdges().not('.collapsed-hop2').addClass('hover-hl');
  }});
  cy.on('mouseout', 'node', function(evt) {{
    var n = evt.target;
    n.removeClass('hover-hl');
    n.connectedEdges().removeClass('hover-hl');
  }});

  // ---------- Filters ----------
  var activeTypes = new Set();
  document.querySelectorAll('#type-filter input[type=checkbox]').forEach(function(cb) {{
    activeTypes.add(cb.dataset.type);
    cb.addEventListener('change', function() {{
      if (cb.checked) activeTypes.add(cb.dataset.type); else activeTypes.delete(cb.dataset.type);
      applyFilters();
    }});
  }});

  var activeHops = new Set([0,1,2,3]);
  document.querySelectorAll('[data-hop]').forEach(function(cb) {{
    cb.addEventListener('change', function() {{
      var h = parseInt(cb.dataset.hop, 10);
      if (cb.checked) activeHops.add(h); else activeHops.delete(h);
      applyFilters();
    }});
  }});

  var searchEl = document.getElementById('search');
  searchEl.addEventListener('input', applyFilters);

  function applyFilters() {{
    var query = (searchEl.value || '').trim().toLowerCase();
    cy.batch(function() {{
      cy.nodes().forEach(function(n) {{
        var d = n.data();
        var hopBucket = d.distance >= 3 ? 3 : (d.distance < 0 ? 3 : d.distance);
        var keep =
          activeTypes.has(d.note_type) &&
          activeHops.has(hopBucket) &&
          (!query || (d.title || '').toLowerCase().indexOf(query) >= 0);
        n.toggleClass('hidden', !keep);
      }});
      cy.edges().forEach(function(e) {{
        var hidden = e.source().hasClass('hidden') || e.target().hasClass('hidden');
        e.toggleClass('hidden', hidden);
      }});
    }});
  }}

  // ---------- Buttons ----------
  document.getElementById('btn-fit').addEventListener('click', function() {{ cy.fit(null, 30); }});
  document.getElementById('btn-seeds').addEventListener('click', function() {{
    var seeds = cy.nodes('.role-seed');
    if (seeds.nonempty()) cy.fit(seeds, 60);
  }});
  document.getElementById('btn-layout').addEventListener('click', function() {{
    cy.layout(layoutOptions(LAYOUT_NAME)).run();
  }});
  document.getElementById('btn-reset').addEventListener('click', function() {{
    searchEl.value = '';
    document.querySelectorAll('#type-filter input').forEach(function(cb) {{ cb.checked = true; activeTypes.add(cb.dataset.type); }});
    document.querySelectorAll('[data-hop]').forEach(function(cb) {{ cb.checked = true; activeHops.add(parseInt(cb.dataset.hop,10)); }});
    applyFilters();
    clearSelection();
    cy.fit(null, 30);
  }});

  // ---------- Hop2 lazy-collapse ----------
  // 大图（>COLLAPSE_THRESHOLD 节点）默认折叠 hop2，点击 hop1 时按需展开。
  // URL 参数可强制覆盖：?collapse_hop2=auto|always|never。
  function getCollapseMode() {{
    var m = (location.search || '').match(/[?&]collapse_hop2=(auto|always|never)/);
    return m ? m[1] : 'auto';
  }}
  function shouldCollapseInitially() {{
    var mode = getCollapseMode();
    if (mode === 'always') return true;
    if (mode === 'never')  return false;
    return INITIAL_AUTO_COLLAPSE;  // auto: server-side default based on size
  }}

  var collapseStatEl = document.getElementById('collapse-stat');
  function updateCollapseStat() {{
    var total = cy.nodes().length;
    var collapsed = cy.nodes('.collapsed-hop2').length;
    var shown = total - collapsed;
    if (collapsed > 0) {{
      collapseStatEl.textContent =
        'Showing ' + shown + ' of ' + total + ' nodes (' + collapsed + ' hop2 collapsed)';
    }} else {{
      collapseStatEl.textContent = 'Showing all ' + total + ' nodes';
    }}
  }}

  // Animated collapse/expand: instead of toggling display:none we tween opacity.
  // The CSS class .collapsed-hop2 sets opacity:0 + events:no, so toggling the
  // class inside cy.batch() alongside Cytoscape's own transition gives a smooth
  // 200ms fade without re-running layout. Edges incident to a collapsed node
  // inherit the class so they fade together.
  var ANIM_MS = 200;
  function _withIncidentEdges(nodes) {{
    return nodes.union(nodes.connectedEdges());
  }}
  function collapseHop2(nodes) {{
    if (nodes.empty()) return;
    var bundle = _withIncidentEdges(nodes);
    cy.batch(function() {{ bundle.addClass('collapsed-hop2'); }});
    setTimeout(updateCollapseStat, ANIM_MS);
    redrawClusters();
  }}
  function expandHop2(nodes) {{
    if (nodes.empty()) return;
    cy.batch(function() {{
      nodes.removeClass('collapsed-hop2');
      // Only un-hide an edge when BOTH endpoints are now visible.
      // (display:none used to handle this for free; opacity:0 doesn't.)
      nodes.connectedEdges().forEach(function(e) {{
        if (!e.source().hasClass('collapsed-hop2') && !e.target().hasClass('collapsed-hop2')) {{
          e.removeClass('collapsed-hop2');
        }}
      }});
    }});
    setTimeout(updateCollapseStat, ANIM_MS);
    redrawClusters();
  }}

  // hop2 candidates = nodes flagged server-side, OR — if absent — fall back
  // to seed_role-based detection so the JS still works without the marker.
  function hop2Nodes() {{
    var marked = cy.nodes('[?collapsed]').filter(function(n) {{
      return n.data('collapsed') === 'hop2';
    }});
    if (marked.nonempty()) return marked;
    return cy.nodes().filter(function(n) {{
      return n.data('seed_role') === 'neighbor_2hop';
    }});
  }}

  // Initial collapse — runs after layout so positions are computed with the
  // full graph in place; subsequent expand simply un-hides at preserved coords.
  if (shouldCollapseInitially()) {{
    collapseHop2(hop2Nodes());
  }} else {{
    updateCollapseStat();
  }}

  // Click a hop1 node → toggle visibility of its hop2 neighbors only.
  cy.on('tap', 'node.role-neighbor_1hop', function(evt) {{
    var hop1 = evt.target;
    var hop2Neighbors = hop1.neighborhood('node').filter(function(n) {{
      return n.data('seed_role') === 'neighbor_2hop';
    }});
    if (hop2Neighbors.empty()) return;
    var anyCollapsed = hop2Neighbors.some(function(n) {{ return n.hasClass('collapsed-hop2'); }});
    if (anyCollapsed) expandHop2(hop2Neighbors);
    else              collapseHop2(hop2Neighbors);
  }});

  document.getElementById('btn-expand-all').addEventListener('click', function() {{
    expandHop2(cy.nodes('.collapsed-hop2'));
  }});
  document.getElementById('btn-collapse-all').addEventListener('click', function() {{
    collapseHop2(hop2Nodes());
  }});
  document.getElementById('btn-expand-selected').addEventListener('click', function() {{
    if (!currentSelection) return;
    var nbrs = currentSelection.neighborhood('node').filter(function(n) {{
      return n.data('seed_role') === 'neighbor_2hop';
    }});
    expandHop2(nbrs);
  }});

  // ---------- Cluster hulls (BubbleSets) ----------
  // Draws a soft envelope around each connected component on a canvas overlay.
  // Falls back gracefully (no-op) if the bubblesets script didn't load — the
  // page still works with all the other affordances. Hulls are recomputed
  // whenever layout shifts, hop2 visibility changes, or the user toggles them.
  // Palette tints come from the dominant note_type per component.
  var BUBBLE_PALETTE = [
    'rgba(52,211,153,0.55)',  // teal — evergreen
    'rgba(167,139,250,0.55)', // violet — essay/project
    'rgba(244,114,182,0.55)', // pink — moc
    'rgba(251,191,36,0.55)',  // amber — article
    'rgba(34,211,238,0.55)',  // cyan — daily/source
    'rgba(248,113,113,0.55)', // red — hop3+
    'rgba(129,140,248,0.55)', // indigo — deep_dive
    'rgba(148,163,184,0.55)'  // slate — raw
  ];
  var bubbleAdapter = null;     // BubbleSets adapter or null on failure / disabled
  var bubblePaths = [];         // tracked so we can remove on redraw
  var clustersEnabled = true;
  var legendEl = document.getElementById('cluster-legend');

  function _registerBubbleSets() {{
    // The UMD bundle exports as window.CytoscapeBubbleSets (PascalCase) and
    // depends on window.CytoscapeLayers being loaded first.
    var lib = window.CytoscapeBubbleSets || window.cytoscapeBubbleSets;
    if (typeof lib === 'undefined') return false;
    try {{ cytoscape.use(lib); return true; }}
    catch (e) {{ return false; }}
  }}

  function _ensureBubbleAdapter() {{
    if (bubbleAdapter) return bubbleAdapter;
    if (!_registerBubbleSets() || typeof cy.bubbleSets !== 'function') return null;
    try {{ bubbleAdapter = cy.bubbleSets(); }}
    catch (e) {{ bubbleAdapter = null; }}
    return bubbleAdapter;
  }}

  function _clearBubblePaths() {{
    if (!bubbleAdapter) return;
    bubblePaths.forEach(function(p) {{
      try {{ bubbleAdapter.removePath(p); }} catch (e) {{ /* ignore */ }}
    }});
    bubblePaths = [];
  }}

  var MIN_CLUSTER_SIZE_FOR_HULL = 3;
  var MAX_LEGEND_ENTRIES = 8;

  function _dominantTypeColor(component) {{
    var counts = {{}};
    component.nodes().forEach(function(n) {{
      var t = n.data('note_type') || 'unknown';
      counts[t] = (counts[t] || 0) + 1;
    }});
    var keys = Object.keys(counts).sort(function(a, b) {{ return counts[b] - counts[a]; }});
    var top = keys[0] || 'unknown';
    var i = Math.abs(_hash(top)) % BUBBLE_PALETTE.length;
    return {{ type: top, color: BUBBLE_PALETTE[i] }};
  }}
  function _hash(s) {{
    var h = 0;
    for (var i = 0; i < s.length; i++) {{ h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }}
    return h;
  }}

  function _renderLegend(entries) {{
    if (!legendEl) return;
    if (!clustersEnabled || !entries.length) {{ legendEl.innerHTML = ''; return; }}
    var html = '';
    entries.slice(0, MAX_LEGEND_ENTRIES).forEach(function(e) {{
      html += '<div><span class="pill" style="background:' + e.color + '"></span>'
            + esc(e.type) + ' (' + e.size + ')</div>';
    }});
    if (entries.length > MAX_LEGEND_ENTRIES) {{
      html += '<div>… +' + (entries.length - MAX_LEGEND_ENTRIES) + ' more</div>';
    }}
    legendEl.innerHTML = html;
  }}

  function redrawClusters() {{
    var adapter = _ensureBubbleAdapter();
    if (!adapter) {{ _renderLegend([]); return; }}
    _clearBubblePaths();
    if (!clustersEnabled) {{ _renderLegend([]); return; }}
    var visibleNodes = cy.nodes().not('.hidden').not('.collapsed-hop2');
    if (visibleNodes.empty()) {{ _renderLegend([]); return; }}
    // CRITICAL: components() needs both nodes AND edges in the collection,
    // otherwise every node looks like its own singleton component and the
    // MIN_CLUSTER_SIZE_FOR_HULL filter drops everything. Include the edges
    // that connect visible nodes.
    var visible = visibleNodes.union(visibleNodes.edgesWith(visibleNodes));
    var components = visible.components();
    var legendEntries = [];
    components.forEach(function(comp) {{
      // Skip trivial singletons — a hull around one node is just visual noise.
      if (comp.nodes().length < MIN_CLUSTER_SIZE_FOR_HULL) return;
      var nodes = comp.nodes();
      var edges = comp.edges();
      var meta = _dominantTypeColor(comp);
      try {{
        var path = adapter.addPath(nodes, edges, null, {{
          virtualEdges: false,
          style: {{
            fill: meta.color,
            stroke: meta.color.replace(/0\.55\)$/, '0.85)'),
            'stroke-width': 1.2
          }}
        }});
        bubblePaths.push(path);
        legendEntries.push({{ type: meta.type, color: meta.color, size: nodes.length }});
      }} catch (e) {{ /* one bad hull shouldn't kill the rest */ }}
    }});
    legendEntries.sort(function(a, b) {{ return b.size - a.size; }});
    _renderLegend(legendEntries);
  }}

  // Toggle in the sidebar — checked = render hulls; unchecked = clear.
  var clusterToggleEl = document.getElementById('cluster-toggle');
  if (clusterToggleEl) {{
    clusterToggleEl.addEventListener('change', function() {{
      clustersEnabled = clusterToggleEl.checked;
      redrawClusters();
    }});
  }}

  // Recompute hulls when layout settles. fcose dispatches layoutstop once at
  // the end of its animation; cose-bilkent does the same.
  cy.on('layoutstop', redrawClusters);
  // Filters change the visible-node set — refresh hulls in lockstep.
  var _origApplyFilters = applyFilters;
  applyFilters = function() {{ _origApplyFilters(); redrawClusters(); }};
  searchEl.removeEventListener('input', _origApplyFilters);
  searchEl.addEventListener('input', applyFilters);

  // Initial focus on seeds if any. The first redrawClusters() runs from
  // layoutstop above once the layout finishes its initial pass.
  var seeds = cy.nodes('.role-seed');
  if (seeds.nonempty()) cy.fit(seeds, 80);
}})();
</script>
</body>
</html>"""


class GraphVisualizer:
    """图谱可视化器"""

    def __init__(self, delta: dict):
        self.delta = delta
        self.nodes = {n['note_id']: n for n in delta.get('nodes', [])}
        self.edges = delta.get('edges', [])

    def ascii(self) -> str:
        """生成ASCII艺术图"""
        lines = []
        lines.append("=" * 60)
        lines.append(f"📊 Daily Delta Graph: {self.delta['day_id']}")
        lines.append("=" * 60)

        # 统计
        stats = self.delta.get('stats', {})
        lines.append("\n📈 统计:")
        lines.append(f"   Seeds: {len(self.delta.get('seed_note_ids', []))}")
        lines.append(f"   Nodes: {stats.get('expanded_node_count', 0)}")
        lines.append(f"   Edges: {stats.get('expanded_edge_count', 0)}")

        # 图例
        lines.append("\n📝 图例:")
        lines.append("   🌱 seed        - 今日新增/修改")
        lines.append("   🔗 1-hop      - 直接关联")
        lines.append("   🔄 2-hop      - 2跳关联")
        lines.append("   📦 3-hop      - 3跳关联")

        # 过滤模板文件
        def is_valid_node(node: dict) -> bool:
            title = node.get('title', '')
            note_id = node.get('note_id', '')
            path = node.get('path', '')
            # 跳过模板占位符
            if '{{' in title or '{{' in note_id:
                return False
            # 跳过模板文件
            if '_template' in path.lower() or note_id.startswith('_'):
                return False
            return True

        # 按类型分组
        by_type = {}
        valid_nodes = []
        for node in self.nodes.values():
            if is_valid_node(node):
                valid_nodes.append(node)
                t = node.get('note_type', 'unknown')
                by_type.setdefault(t, []).append(node)

        lines.append("\n📚 按类型:")
        type_icons = {
            'raw': '📄',
            'deep_dive': '📑',
            'evergreen': '🌲',
            'moc': '🗺️',
            'daily_view': '📅',
        }
        for note_type, nodes in by_type.items():
            icon = type_icons.get(note_type, '📝')
            lines.append(f"   {icon} {note_type}: {len(nodes)}")

        # 节点列表
        lines.append(f"\n🌐 节点 ({len(valid_nodes)}):")
        for node in sorted(valid_nodes, key=lambda n: n.get('seed_role', '')):
            seed_role = node.get('seed_role', 'unknown')
            role_icon = {
                'seed': '🌱',
                'neighbor_1hop': '🔗',
                'neighbor_2hop': '🔄',
                'neighbor_3hop': '📦',
            }.get(seed_role, '❓')

            title = node.get('title', '') or node['note_id'][:30]
            note_type = node.get('note_type', '?')[:8]

            lines.append(f"   {role_icon} [{note_type}] {title}")

        # 边
        if self.edges:
            lines.append(f"\n🔗 边 ({len(self.edges)}):")
            for edge in self.edges[:10]:  # 最多显示10条
                src = edge['source'][:15]
                tgt = edge['target'][:15]
                etype = edge.get('edge_type', '?')[:8]
                lines.append(f"   {src} ──{etype}──> {tgt}")
            if len(self.edges) > 10:
                lines.append(f"   ... 还有 {len(self.edges) - 10} 条边")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)

    def html(
        self,
        output_path: Optional[Path] = None,
        *,
        collapse_hop2_threshold: int = 300,
    ) -> str:
        """生成交互式 HTML 可视化（基于 Cytoscape.js）。

        替代了原来的 vis.js 实现。原版在 ~6K 节点上互动僵硬、信息密度低、
        靠 tooltip 表达；这里改成：
            * 左侧 toolbar：搜索 + note_type 过滤 + hop 过滤 + 布局/聚焦按钮
            * 主画布：fcose 布局（cose-bilkent 作为回退）；连通分量用
              cytoscape-bubblesets 画软包络；节点按 note_type 着色 + 形状区分；
              seed/1hop/2hop 用边框宽度+尺寸表达
            * 右侧 detail panel：点击节点显示 title / type / distance / path /
              入出邻居列表（可点击跳转）
            * 点击节点会高亮邻域（其余节点淡出）

        所有 JS/CSS 走 CDN unpkg；HTML 自包含，生成后直接双击即可打开。
        """
        nodes_data = self._cytoscape_nodes()
        edges_data = self._cytoscape_edges()
        type_counts = self._type_counts(nodes_data)

        # Decide auto-collapse: when the graph is large, mark hop2 nodes so
        # the page boots collapsed (JS can override via ?collapse_hop2=...).
        # Threshold is server-side default; the URL param lets a reviewer flip
        # the decision per-session without re-rendering.
        auto_collapse = len(nodes_data) > collapse_hop2_threshold
        if auto_collapse:
            for node in nodes_data:
                if node['data'].get('seed_role') == 'neighbor_2hop':
                    node['data']['collapsed'] = 'hop2'

        title = self.delta.get('day_id', '') or 'OVP Graph'
        seed_pattern = self.delta.get('seed_pattern', '')
        if seed_pattern:
            title = f"OVP Graph · seed={seed_pattern!r}"

        html = _CYTOSCAPE_TEMPLATE.format(
            page_title=_html_escape(title),
            generated_at=_html_escape(self.delta.get('generated_at', '')),
            node_count=len(nodes_data),
            edge_count=len(edges_data),
            seed_count=len(self.delta.get('seed_note_ids', [])),
            type_filter_html=_render_type_filter(type_counts),
            elements_json=_safe_json({'nodes': nodes_data, 'edges': edges_data}),
            collapse_threshold=collapse_hop2_threshold,
            initial_auto_collapse_js='true' if auto_collapse else 'false',
        )

        if output_path:
            output_path.write_text(html, encoding='utf-8')
            print(f"✅ HTML 已生成: {output_path}")

        return html

    # ---------- Cytoscape data shaping ----------

    def _cytoscape_nodes(self) -> list[dict]:
        out = []
        for node in self.delta.get('nodes', []):
            note_type = (node.get('note_type') or 'unknown')
            seed_role = node.get('seed_role') or 'unknown'
            distance = node.get('distance_from_seed', None)
            title = node.get('title') or node.get('note_id', '')
            label = title if len(title) <= 32 else title[:30] + '…'
            out.append({
                'data': {
                    'id': node['note_id'],
                    'label': label,
                    'title': title,
                    'note_type': note_type,
                    'seed_role': seed_role,
                    'distance': distance if distance is not None else -1,
                    'path': node.get('path', '') or '',
                    'day_id': node.get('day_id', '') or '',
                },
                'classes': ' '.join([
                    f"type-{_safe_class(note_type)}",
                    f"role-{_safe_class(seed_role)}",
                ]),
            })
        return out

    def _cytoscape_edges(self) -> list[dict]:
        out = []
        for edge in self.delta.get('edges', []):
            edge_type = edge.get('edge_type') or 'wikilink'
            out.append({
                'data': {
                    'id': edge.get('edge_id') or f"{edge['source']}-{edge['target']}",
                    'source': edge['source'],
                    'target': edge['target'],
                    'edge_type': edge_type,
                    'anchor_text': edge.get('anchor_text', '') or '',
                },
                'classes': f"edgetype-{_safe_class(edge_type)}",
            })
        return out

    @staticmethod
    def _type_counts(nodes_data: list[dict]) -> list[tuple[str, int]]:
        from collections import Counter
        counter: Counter = Counter(n['data']['note_type'] for n in nodes_data)
        # 稳定顺序：先按数量降序，再按名字升序
        return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))

    def export_graphml(self, output_path: Path):
        """导出为GraphML格式 (兼容Gephi, yEd)"""
        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">')

        # 定义节点属性
        lines.append('  <key id="title" for="node" attr.name="title" attr.type="string"/>')
        lines.append('  <key id="note_type" for="node" attr.name="note_type" attr.type="string"/>')
        lines.append('  <key id="seed_role" for="node" attr.name="seed_role" attr.type="string"/>')
        lines.append('  <key id="day_id" for="node" attr.name="day_id" attr.type="string"/>')
        lines.append('  <key id="path" for="node" attr.name="path" attr.type="string"/>')
        lines.append('  <key id="edge_type" for="edge" attr.name="edge_type" attr.type="string"/>')

        lines.append('  <graph id="G" edgedefault="directed">')

        # 节点
        for node in self.delta.get('nodes', []):
            nid = node['note_id']
            title = node.get('title', '') or ''
            note_type = node.get('note_type', '')
            seed_role = node.get('seed_role', '')
            day_id = node.get('day_id', '')
            path = node.get('path', '')

            lines.append(f'    <node id="{nid}">')
            lines.append(f'      <data key="title">{self._escape_xml(title)}</data>')
            lines.append(f'      <data key="note_type">{note_type}</data>')
            lines.append(f'      <data key="seed_role">{seed_role}</data>')
            lines.append(f'      <data key="day_id">{day_id}</data>')
            lines.append(f'      <data key="path">{self._escape_xml(path)}</data>')
            lines.append('    </node>')

        # 边
        for edge in self.delta.get('edges', []):
            eid = edge.get('edge_id', f"{edge['source']}-{edge['target']}")
            edge_type = edge.get('edge_type', '')
            lines.append(f'    <edge id="{eid}" source="{edge["source"]}" target="{edge["target"]}">')
            lines.append(f'      <data key="edge_type">{edge_type}</data>')
            lines.append('    </edge>')

        lines.append('  </graph>')
        lines.append('</graphml>')

        output_path.write_text('\n'.join(lines), encoding='utf-8')
        print(f"✅ GraphML已导出: {output_path}")

    @staticmethod
    def _escape_xml(s: str) -> str:
        """转义XML特殊字符"""
        return (s.replace('&', '&amp;')
                  .replace('<', '&lt;')
                  .replace('>', '&gt;')
                  .replace('"', '&quot;')
                  .replace("'", '&apos;'))
