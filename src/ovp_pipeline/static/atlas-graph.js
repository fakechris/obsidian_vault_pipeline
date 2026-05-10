/* ============================================================
   OVP Cluster Graph — application logic
   3d-force-graph + custom hulls/labels + filters/timeline/tweaks
   ============================================================ */

(function () {
  const data = window.OVP_GRAPH;
  // Vault is empty (no clusters / no nodes after filters) — keep the
  // chrome rendered server-side and bail before three.js touches the
  // canvas.  The #atlas-empty div is already shown by the server.
  if (!data || !Array.isArray(data.nodes) || data.nodes.length === 0) {
    const empty = document.getElementById("atlas-empty");
    if (empty) empty.style.display = "flex";
    // Tweaks/timeline rely on the simulation; hide them so they
    // don't look broken on an empty vault.
    ["tweaks", "detail"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = "none";
    });
    const tl = document.querySelector(".timeline-bar");
    if (tl) tl.style.display = "none";
    // Theme toggle still works — wire it stand-alone.
    document.querySelectorAll('[data-theme-set]').forEach(b => {
      b.addEventListener("click", () => {
        const v = b.dataset.themeSet;
        document.documentElement.setAttribute("data-theme", v);
        document.querySelectorAll('[data-theme-set]').forEach(x =>
          x.classList.toggle("active", x.dataset.themeSet === v));
        try { localStorage.setItem("ovp-theme", v); } catch (e) {}
      });
    });
    return;
  }

  // ---------- THEME / TOKENS ----------
  const html = document.documentElement;

  function readToken(name) {
    return getComputedStyle(html).getPropertyValue(name).trim();
  }

  // Escape anything that flows into ``innerHTML`` from the vault
  // payload (community names, node labels, sources, dates, etc.).
  // Vault content is technically authored by the user, but the
  // graph runs in the same origin as the rest of the maintenance
  // UI — a malicious or pasted-in label must not be able to break
  // out of the surrounding HTML.
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function communityColor(cid) {
    const c = data.communities.find(x => x.id === cid);
    if (!c) return "#888";
    const n = +c.color.match(/\d+/)[0];
    return readToken(`--c-${n}`);
  }

  // ---------- STATE ----------
  // ``superNodeMode`` (off by default) is the only path that
  // collapses a community into a single super-node.  Default view
  // keeps every community expanded so the user sees the full graph
  // density (objects + their cross-community links) at first paint.
  // Double-click on a node now toggles community **isolation**, not
  // collapse — focuses the view on that one community without
  // creating a giant blob.
  const state = {
    isolatedCommunities: new Set(),         // empty = all communities visible
    types: new Set(["evergreen","deepdive","topic","open-question"]),
    sources: new Set(["manual","pinboard","clipper","github"]),
    qualityMin: 0,
    expandedCommunities: new Set(),         // populated below — all expanded by default
    superNodeMode: false,                   // opt-in collapse via Tweaks panel
    mode: "dblclick",                       // dblclick | hover | zoom
    showHulls: true,
    spin: "off",
    selectedNodeId: null,
    hoverNodeId: null,
    timelineDate: null,                     // null = all time
    playing: false,
    playTimer: null,
  };

  // All communities expanded by default so the user sees a dense,
  // navigable graph instead of 24 lonely super-nodes.
  data.communities.forEach(c => state.expandedCommunities.add(c.id));

  // Deep-link support: ``/map?community=<cluster_id>`` from the
  // cluster detail page pre-isolates that community so the user
  // arrives focused on the cluster they were just reading about.
  // Same shell, same tech as the unscoped /map view.
  try {
    const qs = new URLSearchParams(window.location.search);
    const community = qs.get("community");
    if (community
        && data.communities.some(c => c.id === community)) {
      state.isolatedCommunities = new Set([community]);
    }
  } catch (e) {}

  // ---------- PARSE DATES ----------
  const allDates = data.nodes.map(n => new Date(n.absorbedAt + "T12:00:00")).sort((a,b)=>a-b);
  const dateMin = allDates[0], dateMax = allDates[allDates.length-1];
  const DAY = 86400000;
  const dateBuckets = (() => {
    const days = Math.ceil((dateMax - dateMin) / DAY);
    const buckets = new Array(Math.min(days+1, 80)).fill(0);
    const step = (dateMax - dateMin) / buckets.length || DAY;
    data.nodes.forEach(n => {
      const idx = Math.min(buckets.length-1, Math.floor((new Date(n.absorbedAt+"T12:00:00") - dateMin) / step));
      buckets[idx]++;
    });
    return { buckets, step };
  })();

  // ---------- BUILD FILTERED VIEW ----------
  function nodePassesFilters(n) {
    if (!state.types.has(n.type)) return false;
    if (!state.sources.has(n.source)) return false;
    if (n.quality !== null && n.quality < state.qualityMin) return false;
    if (state.qualityMin > 0 && n.quality === null) return false;
    if (state.isolatedCommunities.size && !state.isolatedCommunities.has(n.community)) return false;
    if (state.timelineDate) {
      const nd = new Date(n.absorbedAt + "T12:00:00");
      if (nd > state.timelineDate) return false;
    }
    return true;
  }

  function buildGraph() {
    const passing = data.nodes.filter(nodePassesFilters);
    const passingIds = new Set(passing.map(n => n.id));

    // Decide which communities are expanded vs collapsed
    const nodes = [];
    const idMap = {}; // original id -> rendered id

    // A community renders as collapsed super-node only when global
    // ``superNodeMode`` is on AND the user hasn't opted that one
    // community into the expanded set.  When superNodeMode is off
    // (default), every community renders as full member nodes.
    data.communities.forEach(c => {
      const members = passing.filter(n => n.community === c.id);
      if (members.length === 0) return;

      const collapsed =
        state.superNodeMode && !state.expandedCommunities.has(c.id);

      if (!collapsed) {
        members.forEach(n => {
          const out = {
            ...n,
            __cidColor: communityColor(c.id),
            __isSuper: false,
          };
          nodes.push(out);
          idMap[n.id] = n.id;
        });
      } else {
        // Super node — only reached when superNodeMode is on
        const sid = "S_" + c.id;
        nodes.push({
          id: sid,
          label: c.name,
          type: "super",
          community: c.id,
          quality: 4.5,
          backlinks: members.length,
          openQuestion: false,
          source: "manual",
          absorbedAt: c.id,
          __cidColor: communityColor(c.id),
          __isSuper: true,
          __memberCount: members.length,
        });
        members.forEach(n => { idMap[n.id] = sid; });
      }
    });

    // Links: collapse ids and dedupe, drop self-loops
    const seenLinkKey = new Set();
    const links = [];
    data.links.forEach(l => {
      const sId = (typeof l.source === "object" ? l.source.id : l.source);
      const tId = (typeof l.target === "object" ? l.target.id : l.target);
      if (!passingIds.has(sId) || !passingIds.has(tId)) return;
      const a = idMap[sId];
      const b = idMap[tId];
      if (!a || !b || a === b) return;
      const key = a < b ? a + "|" + b + "|" + l.kind : b + "|" + a + "|" + l.kind;
      if (seenLinkKey.has(key)) return;
      seenLinkKey.add(key);
      links.push({ source: a, target: b, kind: l.kind });
    });

    return { nodes, links };
  }

  // ---------- THREE / GRAPH SETUP ----------
  const el = document.getElementById("graph-canvas");
  const Graph = ForceGraph3D({ controlType: 'orbit' })(el);

  Graph
    .backgroundColor("rgba(0,0,0,0)")
    .nodeRelSize(4)
    .nodeOpacity(0.98)
    .linkOpacity(0.55)
    .linkWidth(l => l.kind === "contradict" ? 1.4 : 0.6)
    .linkColor(l => {
      if (l.kind === "contradict") return readToken("--warn-text") || "#f59e0b";
      if (l.kind === "cite") return readToken("--accent") || "#3b82f6";
      return readToken("--border-strong") || "#7d8aa0";
    })
    .linkDirectionalParticles(l => l.kind === "cite" ? 2 : 0)
    .linkDirectionalParticleWidth(1)
    .linkDirectionalParticleSpeed(0.005)
    .nodeThreeObject(node => {
      // Node = sphere + text label sprite (shown when hovered/selected via opacity tween)
      const group = new THREE.Group();

      const sizeBase = +document.getElementById("node-size").value;
      let r = sizeBase;
      if (node.__isSuper) r = sizeBase * 2.4 + Math.sqrt(node.__memberCount) * 0.8;
      else if (node.type === "topic") r = sizeBase * 1.6;
      else if (node.type === "deepdive") r = sizeBase * 1.25;
      else if (node.type === "open-question") r = sizeBase * 1.1;
      else if (node.quality != null) r = sizeBase * (0.8 + node.quality / 6);
      node.__r = r;

      // shape choice
      let geom;
      if (node.type === "open-question") geom = new THREE.OctahedronGeometry(r, 0);
      else if (node.type === "topic") geom = new THREE.BoxGeometry(r*1.6, r*1.6, r*1.6);
      else if (node.type === "deepdive") geom = new THREE.IcosahedronGeometry(r, 0);
      else geom = new THREE.SphereGeometry(r, 16, 12);

      const color = node.__cidColor;
      const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.92 });
      const mesh = new THREE.Mesh(geom, mat);
      group.add(mesh);

      // Halo for open-question
      if (node.type === "open-question") {
        const halo = new THREE.Mesh(
          new THREE.SphereGeometry(r * 1.5, 16, 12),
          new THREE.MeshBasicMaterial({ color: readToken("--warn-text") || "#f59e0b", transparent: true, opacity: 0.18 })
        );
        group.add(halo);
      }

      // text label sprite (always for super, hidden by default for regulars)
      if (node.__isSuper || node.type === "topic" || (node.quality && node.quality >= 4.4)) {
        const sprite = makeTextSprite(node.label, node.__isSuper);
        sprite.position.y = r + 4;
        group.add(sprite);
        node.__label = sprite;
      }
      return group;
    })
    .nodeThreeObjectExtend(false)
    .onNodeHover(handleHover)
    .onNodeClick(handleClick)
    .onBackgroundClick(() => {
      state.selectedNodeId = null; renderDetail(); refreshHighlight();
    })
    .d3Force("charge").strength(+document.getElementById("link-strength").value);

  Graph.d3Force("link").distance(l => l.kind === "cite" ? 22 : 18).strength(0.35);

  // Custom rendering hook for hulls (community blobs) — we draw a transparent sphere per community centroid.
  const hullGroup = new THREE.Group();
  Graph.scene().add(hullGroup);

  // Refit on resize
  function resize() { Graph.width(el.clientWidth).height(el.clientHeight); }
  resize();
  window.addEventListener("resize", resize);

  // Periodic hull update tied to the simulation
  setInterval(updateHulls, 80);

  function updateHulls() {
    if (!state.showHulls) { hullGroup.visible = false; return; }
    hullGroup.visible = true;
    // group nodes by community
    const byCom = {};
    Graph.graphData().nodes.forEach(n => {
      if (n.x == null) return;
      (byCom[n.community] = byCom[n.community] || []).push(n);
    });

    // Reuse existing meshes
    const existing = hullGroup.children;
    let i = 0;
    Object.entries(byCom).forEach(([cid, ns]) => {
      if (ns.length < 1) return;
      // centroid
      let cx=0,cy=0,cz=0; ns.forEach(n=>{cx+=n.x;cy+=n.y;cz+=n.z;});
      cx/=ns.length; cy/=ns.length; cz/=ns.length;
      // radius
      let maxD=0;
      ns.forEach(n=>{
        const d = Math.hypot(n.x-cx,n.y-cy,n.z-cz);
        if (d>maxD) maxD = d;
      });
      const r = Math.max(maxD + 8, 10);
      let mesh = existing[i];
      if (!mesh) {
        mesh = new THREE.Mesh(
          new THREE.SphereGeometry(1, 24, 18),
          new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.07, side: THREE.BackSide, depthWrite: false })
        );
        hullGroup.add(mesh);
      }
      const isolated = state.isolatedCommunities.size && !state.isolatedCommunities.has(cid);
      mesh.visible = true;
      mesh.position.set(cx,cy,cz);
      mesh.scale.setScalar(r);
      mesh.material.color.set(communityColor(cid));
      mesh.material.opacity = isolated ? 0.02 : 0.09;
      i++;
    });
    // hide unused
    for (; i<existing.length; i++) existing[i].visible = false;
  }

  // ---------- TEXT SPRITE ----------
  function makeTextSprite(text, isBig) {
    const fontSize = isBig ? 32 : 22;
    const pad = 8;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    ctx.font = `${isBig?700:600} ${fontSize}px Inter, sans-serif`;
    const w = ctx.measureText(text).width + pad*2;
    const h = fontSize + pad*1.2;
    canvas.width = w; canvas.height = h;
    ctx.font = `${isBig?700:600} ${fontSize}px Inter, sans-serif`;
    ctx.textBaseline = "middle";
    ctx.fillStyle = readToken("--text") || "#fff";
    ctx.fillText(text, pad, h/2);
    const tex = new THREE.CanvasTexture(canvas);
    tex.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    const scl = isBig ? 0.55 : 0.4;
    sprite.scale.set(w*scl/10, h*scl/10, 1);
    return sprite;
  }

  // ---------- INTERACTION ----------
  function handleHover(node) {
    state.hoverNodeId = node ? node.id : null;
    const lbl = document.getElementById("hover-label");
    if (node) {
      el.style.cursor = "pointer";
      lbl.style.display = "block";
      lbl.querySelector(".ttl").textContent = node.label;
      const c = data.communities.find(c=>c.id===node.community);
      let sub = c ? c.name : "";
      if (node.__isSuper) sub = c.name + " · " + node.__memberCount + " notes (collapsed)";
      else if (node.type === "open-question") sub = "Open question · " + (c ? c.name : "");
      else if (node.type === "topic") sub = "Topic · " + (c ? c.name : "");
      else if (node.type === "deepdive") sub = "Deep dive · " + (c ? c.name : "");
      lbl.querySelector(".sub").textContent = sub;
      // position via screen coords
      const screen = Graph.graph2ScreenCoords ? Graph.graph2ScreenCoords(node.x, node.y, node.z) : null;
      if (screen) { lbl.style.left = screen.x + "px"; lbl.style.top  = screen.y + "px"; }
      else { lbl.style.left = "50%"; lbl.style.top = "50%"; }

      if (state.mode === "hover" && node.__isSuper) {
        // expand on hover
        state.expandedCommunities.add(node.community);
        rebuild();
      }
    } else {
      el.style.cursor = "default";
      lbl.style.display = "none";
    }
    refreshHighlight();
  }

  function handleClick(node) {
    if (!node) return;
    state.selectedNodeId = node.id;
    if (node.__isSuper) {
      // Single click on super node: zoom to it
      Graph.cameraPosition(
        { x: node.x*1.4, y: node.y*1.4, z: node.z*1.4 },
        node, 700
      );
    } else {
      Graph.cameraPosition(
        { x: node.x + 30, y: node.y + 10, z: node.z + 60 },
        node, 700
      );
    }
    renderDetail();
    refreshHighlight();
  }

  // Double-click on any node now isolates the view to that node's
  // community (or restores the full view if already isolated to it).
  // This replaces the old "collapse to super-node" behaviour, which
  // produced a giant blob with no visible structure.  Super-node
  // collapse stays available via the explicit Tweaks toggle.
  el.addEventListener("dblclick", () => {
    const node = state.hoverNodeId
      ? Graph.graphData().nodes.find(n => n.id === state.hoverNodeId)
      : null;
    if (!node || state.mode !== "dblclick") return;
    if (node.__isSuper) {
      // Super-node dblclick: drop super mode and isolate the
      // community, so the user sees its members rather than a blob.
      state.superNodeMode = false;
      const segSuper = document.getElementById("seg-super");
      if (segSuper) {
        segSuper.querySelectorAll("button").forEach(b =>
          b.classList.toggle("active", b.dataset.super === "off")
        );
      }
      state.isolatedCommunities = new Set([node.community]);
    } else if (state.isolatedCommunities.has(node.community)
               && state.isolatedCommunities.size === 1) {
      // Already isolated to this community → restore full view.
      state.isolatedCommunities.clear();
    } else {
      state.isolatedCommunities = new Set([node.community]);
    }
    rebuild();
    syncLegendDim();
  });

  function syncLegendDim() {
    const legendEl = document.getElementById("legend");
    if (!legendEl) return;
    legendEl.querySelectorAll(".legend-row").forEach(r => {
      const dim = state.isolatedCommunities.size
        && !state.isolatedCommunities.has(r.dataset.cid);
      r.classList.toggle("dim", dim);
    });
  }

  // Zoom-based disclosure only applies when the user has opted into
  // super-node mode AND chosen the zoom mode in Tweaks.  Otherwise
  // every community is always expanded.
  Graph.onEngineTick(() => {
    if (state.mode !== "zoom" || !state.superNodeMode) return;
    const pos = Graph.cameraPosition();
    const dist = Math.hypot(pos.x, pos.y, pos.z);
    if (dist < 280) {
      let changed = false;
      data.communities.forEach(c => {
        if (!state.expandedCommunities.has(c.id)) {
          state.expandedCommunities.add(c.id);
          changed = true;
        }
      });
      if (changed) rebuild();
    } else if (dist > 520) {
      let changed = false;
      data.communities.forEach(c => {
        if (state.expandedCommunities.has(c.id)) {
          state.expandedCommunities.delete(c.id);
          changed = true;
        }
      });
      if (changed) rebuild();
    }
  });

  function refreshHighlight() {
    // dim non-relevant when hovering or selecting
    const focus = state.selectedNodeId || state.hoverNodeId;
    Graph.nodeOpacity(0.9);
    if (!focus) {
      Graph.linkOpacity(0.4);
      return;
    }
    const focused = Graph.graphData().nodes.find(n => n.id === focus);
    if (!focused) return;
    const neigh = new Set([focused.id]);
    Graph.graphData().links.forEach(l => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (s === focused.id) neigh.add(t);
      if (t === focused.id) neigh.add(s);
    });
    Graph.linkOpacity(0.7);
    // Visualize via per-node opacity by re-using color material
    Graph.graphData().nodes.forEach(n => {
      if (!n.__threeObj) return;
      n.__threeObj.children.forEach(c => {
        if (c.material && "opacity" in c.material) {
          c.material.opacity = neigh.has(n.id) ? 1.0 : 0.18;
        }
      });
    });
  }

  function rebuild() {
    const g = buildGraph();
    Graph.graphData(g);
    document.getElementById("hud-nodes").textContent = g.nodes.length;
    document.getElementById("hud-links").textContent = g.links.length;
  }

  // ---------- LEGEND ----------
  const legend = document.getElementById("legend");
  data.communities.forEach(c => {
    const row = document.createElement("div");
    row.className = "legend-row";
    row.dataset.cid = c.id;
    row.innerHTML = `<span class="swatch" style="background:${esc(communityColor(c.id))}"></span>
                     <span class="name">${esc(c.name)}</span>
                     <span class="count">${esc(c.count)}</span>`;
    row.addEventListener("click", e => {
      if (e.shiftKey) {
        if (state.isolatedCommunities.has(c.id)) state.isolatedCommunities.delete(c.id);
        else state.isolatedCommunities.add(c.id);
      } else {
        if (state.isolatedCommunities.size === 1 && state.isolatedCommunities.has(c.id)) {
          state.isolatedCommunities.clear();
        } else {
          state.isolatedCommunities.clear();
          state.isolatedCommunities.add(c.id);
        }
      }
      legend.querySelectorAll(".legend-row").forEach(r => {
        const dim = state.isolatedCommunities.size && !state.isolatedCommunities.has(r.dataset.cid);
        r.classList.toggle("dim", dim);
      });
      rebuild();
    });
    legend.appendChild(row);
  });
  // If a deep-link pre-isolated a community, dim the rest now that
  // the legend rows exist.
  syncLegendDim();

  // type/source counts
  const typeCounts = {evergreen:0,deepdive:0,topic:0,"open-question":0};
  data.nodes.forEach(n => { if (n.type in typeCounts) typeCounts[n.type]++; });
  Object.entries(typeCounts).forEach(([k,v]) => {
    const el = document.getElementById("cnt-" + k);
    if (el) el.textContent = v;
  });

  document.querySelectorAll('input[data-filter]').forEach(inp => {
    inp.addEventListener("change", () => {
      if (inp.checked) state.types.add(inp.dataset.filter);
      else state.types.delete(inp.dataset.filter);
      rebuild();
    });
  });
  document.querySelectorAll('input[data-filter-src]').forEach(inp => {
    inp.addEventListener("change", () => {
      if (inp.checked) state.sources.add(inp.dataset.filterSrc);
      else state.sources.delete(inp.dataset.filterSrc);
      rebuild();
    });
  });
  document.getElementById("quality-slider").addEventListener("input", e => {
    state.qualityMin = +e.target.value;
    document.getElementById("quality-val").textContent = state.qualityMin.toFixed(1);
    rebuild();
  });

  // ---------- TWEAKS ----------
  function bindSeg(id, dataAttr, cb) {
    const seg = document.getElementById(id);
    seg.querySelectorAll("button").forEach(b => {
      b.addEventListener("click", () => {
        seg.querySelectorAll("button").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        cb(b.dataset[dataAttr]);
      });
    });
  }
  bindSeg("seg-mode", "mode", v => {
    state.mode = v;
    document.getElementById("hud-mode").innerHTML = "<strong>" + ({dblclick:"EXPAND",hover:"HOVER",zoom:"ZOOM"}[v]) + "</strong>" + ({dblclick:"double-click",hover:"hover supernode",zoom:"close to expand"}[v]);
  });
  bindSeg("seg-hulls", "hulls", v => { state.showHulls = (v === "on"); });
  bindSeg("seg-spin", "spin", v => { state.spin = v; });
  // Communities: Expanded (default) shows every member node;
  // Collapsed renders one super-node per community.  When the user
  // toggles to Collapsed, drop everything from expandedCommunities so
  // each community renders as a super-node.
  const segSuper = document.getElementById("seg-super");
  if (segSuper) {
    bindSeg("seg-super", "super", v => {
      state.superNodeMode = (v === "on");
      if (v === "on") {
        state.expandedCommunities.clear();
      } else {
        data.communities.forEach(c => state.expandedCommunities.add(c.id));
      }
      rebuild();
    });
  }

  // 2D / 3D toggle.  ``currentDimensions`` mirrors what the
  // simulation is actually rendering so other handlers (e.g. the
  // link-strength slider) can re-apply it without forcing the
  // graph back to 3D when the user is in 2D mode.
  let currentDimensions = 3;
  function setDimensions(n) {
    currentDimensions = +n;
    Graph.numDimensions(currentDimensions);
    const ctrl = Graph.controls();
    if (currentDimensions === 2) {
      // top-down camera, disable rotation
      Graph.cameraPosition({ x: 0, y: 0, z: 420 }, { x: 0, y: 0, z: 0 }, 700);
      if (ctrl) { ctrl.enableRotate = false; }
    } else {
      Graph.cameraPosition({ x: 60, y: 80, z: 380 }, { x: 0, y: 0, z: 0 }, 700);
      if (ctrl) { ctrl.enableRotate = true; }
    }
    Graph.d3ReheatSimulation();
  }
  bindSeg("seg-dims", "dims", setDimensions);

  document.getElementById("link-strength").addEventListener("input", e => {
    document.getElementById("link-strength-val").textContent = e.target.value;
    Graph.d3Force("charge").strength(+e.target.value);
    // Re-apply the active dimension count to nudge the simulation
    // back into a stable layout — must use ``currentDimensions``,
    // not a hard-coded 3, or 2D mode silently exits when the user
    // drags the link-strength slider.
    Graph.numDimensions(currentDimensions);
    Graph.d3ReheatSimulation();
  });
  document.getElementById("node-size").addEventListener("input", e => {
    document.getElementById("node-size-val").textContent = (+e.target.value).toFixed(1);
    rebuild(); // simplest path: rebuild objects
  });

  // ---------- SEARCH ----------
  const search = document.getElementById("search");
  const resultsEl = document.getElementById("search-results");
  function renderSearch(q) {
    if (!q) { resultsEl.classList.remove("open"); resultsEl.innerHTML = ""; return; }
    const ql = q.toLowerCase();
    const hits = data.nodes.filter(n => n.label.toLowerCase().includes(ql)).slice(0,8);
    if (hits.length === 0) {
      resultsEl.innerHTML = '<div class="empty">No matches</div>';
    } else {
      resultsEl.innerHTML = hits.map(n => {
        const c = data.communities.find(c=>c.id===n.community);
        return `<div class="row" data-id="${esc(n.id)}">
          <span class="swatch" style="background:${esc(communityColor(n.community))}"></span>
          <span>${esc(n.label)}</span>
          <span class="meta">${esc(c?c.name:"")}</span>
        </div>`;
      }).join("");
      resultsEl.querySelectorAll(".row").forEach(r => {
        r.addEventListener("click", () => {
          const id = r.dataset.id;
          // make sure community expanded so node exists
          const orig = data.nodes.find(n => n.id === id);
          if (orig) state.expandedCommunities.add(orig.community);
          rebuild();
          setTimeout(() => {
            const node = Graph.graphData().nodes.find(n => n.id === id);
            if (node) handleClick(node);
            search.value = "";
            renderSearch("");
          }, 50);
        });
      });
    }
    resultsEl.classList.add("open");
  }
  search.addEventListener("input", e => renderSearch(e.target.value));
  search.addEventListener("focus", e => { if (e.target.value) renderSearch(e.target.value); });
  search.addEventListener("blur", () => setTimeout(()=>renderSearch(""),200));
  document.addEventListener("keydown", e => {
    if (e.key === "/" && document.activeElement !== search) {
      e.preventDefault(); search.focus();
    }
    if (e.key === "Escape") { search.blur(); renderSearch(""); }
  });

  // ---------- DETAIL PANEL ----------
  function renderDetail() {
    const wrap = document.getElementById("detail");
    if (!state.selectedNodeId) {
      wrap.innerHTML = `<div class="detail-empty">
        <div class="glyph">⌖</div>
        <div>Click a node to inspect.</div>
        <div style="margin-top:6px;font-size:0.78rem">Or hover to highlight neighbors.</div>
      </div>`;
      return;
    }
    const orig = data.nodes.find(n => n.id === state.selectedNodeId);
    const isSuper = state.selectedNodeId.startsWith("S_");
    const c = isSuper
      ? data.communities.find(c => "S_" + c.id === state.selectedNodeId)
      : data.communities.find(c => c.id === orig.community);

    if (isSuper) {
      const members = data.nodes.filter(n => n.community === c.id).filter(nodePassesFilters);
      wrap.innerHTML = `
        <div class="detail-head">
          <div class="typewrap">
            <span class="pill" style="background:${esc(communityColor(c.id))};color:white">Community</span>
          </div>
          <h2>${esc(c.name)}</h2>
          <div class="community-line">
            <span class="swatch" style="background:${esc(communityColor(c.id))}"></span>
            <span>${esc(members.length)} notes · collapsed view</span>
          </div>
          <div class="actions">
            <button class="btn" id="btn-expand">Expand cluster</button>
            <button class="btn ghost" id="btn-isolate">Isolate</button>
          </div>
        </div>
        <div class="neighbors">
          <h5>Top notes by quality</h5>
          ${members.sort((a,b)=>(b.quality||0)-(a.quality||0)).slice(0,12).map(m=>`
            <div class="nrow" data-id="${esc(m.id)}">
              <span class="swatch" style="background:${esc(communityColor(m.community))}"></span>
              <span>${esc(m.label)}</span>
              <span class="kindtag">${esc(m.type)}</span>
            </div>
          `).join("")}
        </div>`;
      document.getElementById("btn-expand").addEventListener("click", ()=>{
        state.expandedCommunities.add(c.id); rebuild();
      });
      document.getElementById("btn-isolate").addEventListener("click", ()=>{
        state.isolatedCommunities.clear();
        state.isolatedCommunities.add(c.id);
        legend.querySelectorAll(".legend-row").forEach(r => {
          r.classList.toggle("dim", r.dataset.cid !== c.id);
        });
        rebuild();
      });
      wrap.querySelectorAll(".nrow").forEach(r => r.addEventListener("click", () => {
        state.expandedCommunities.add(c.id);
        rebuild();
        setTimeout(() => {
          const n = Graph.graphData().nodes.find(x => x.id === r.dataset.id);
          if (n) handleClick(n);
        }, 80);
      }));
      return;
    }

    // Regular node
    const neighborLinks = data.links.filter(l => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return s === orig.id || t === orig.id;
    });
    const neighbors = neighborLinks.map(l => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      const otherId = s === orig.id ? t : s;
      const other = data.nodes.find(n => n.id === otherId);
      return { other, kind: l.kind };
    }).filter(x => x.other);

    const typeLabel = {evergreen:"Evergreen",deepdive:"Deep dive",topic:"Topic","open-question":"Open question"}[orig.type];
    wrap.innerHTML = `
      <div class="detail-head">
        <div class="typewrap">
          <span class="pill ${orig.type==='open-question'?'warn':''}">${esc(typeLabel)}</span>
          ${orig.quality?`<span class="muted tiny mono">QUAL ${esc(orig.quality.toFixed(1))}</span>`:''}
          <span class="muted tiny mono">SRC ${esc(orig.source)}</span>
        </div>
        <h2>${esc(orig.label)}</h2>
        <div class="community-line">
          <span class="swatch" style="background:${esc(communityColor(orig.community))}"></span>
          <span>${esc(c?c.name:"")} · ${esc(orig.backlinks)} backlinks · absorbed ${esc(orig.absorbedAt)}</span>
        </div>
        <div class="actions">
          <button class="btn" id="btn-open-vault"${orig.path ? "" : " disabled"}>Open in vault →</button>
          <button class="btn ghost" id="btn-focus">Focus subgraph</button>
        </div>
      </div>
      <div class="neighbors">
        <h5>Linked notes (${esc(neighbors.length)})</h5>
        ${neighbors.length ? neighbors.map(({other, kind})=>`
          <div class="nrow" data-id="${esc(other.id)}">
            <span class="swatch" style="background:${esc(communityColor(other.community))}"></span>
            <span>${esc(other.label)}</span>
            <span class="kindtag ${kind==='contradict'?'contradict':''}">${esc(kind)}</span>
          </div>
        `).join("") : '<div class="muted tiny" style="padding:6px 8px">No links yet.</div>'}
      </div>`;
    const btnOpen = document.getElementById("btn-open-vault");
    if (btnOpen && orig.path) {
      btnOpen.addEventListener("click", () => {
        window.location.href = orig.path;
      });
    }
    document.getElementById("btn-focus").addEventListener("click", ()=>{
      state.isolatedCommunities.clear();
      state.isolatedCommunities.add(orig.community);
      legend.querySelectorAll(".legend-row").forEach(r => {
        r.classList.toggle("dim", r.dataset.cid !== orig.community);
      });
      rebuild();
    });
    wrap.querySelectorAll(".nrow").forEach(r => r.addEventListener("click", () => {
      const otherOrig = data.nodes.find(x => x.id === r.dataset.id);
      if (otherOrig) state.expandedCommunities.add(otherOrig.community);
      rebuild();
      setTimeout(() => {
        const n = Graph.graphData().nodes.find(x => x.id === r.dataset.id);
        if (n) handleClick(n);
      }, 80);
    }));
  }

  // ---------- TIMELINE ----------
  const scrubber = (() => {
    const bar = document.querySelector(".timeline-bar");
    bar.innerHTML = `
      <div class="head">
        <span class="label">ABSORBED ≤</span>
        <span class="date" id="tl-date">all time</span>
        <div class="actions">
          <button id="tl-reset">Reset</button>
          <button id="tl-play">▶ Play history</button>
        </div>
      </div>
      <div class="scrubber" id="tl-scrub">
        <div class="ticks">${Array.from({length:12}).map(()=>'<div class="tick"></div>').join('')}</div>
        <div class="bars" id="tl-bars">${dateBuckets.buckets.map(c=>`<div class="bar" style="height:${Math.max(8,c*8)}%"></div>`).join('')}</div>
        <div class="head-line" id="tl-head" style="left:100%"></div>
        <div class="label-axis"><span>${fmtDate(dateMin)}</span><span>${fmtDate(dateMax)}</span></div>
      </div>`;
    return bar.querySelector("#tl-scrub");
  })();
  function fmtDate(d) { return d.toISOString().slice(0,10); }
  function setTimelineFraction(f) {
    f = Math.max(0, Math.min(1, f));
    document.getElementById("tl-head").style.left = (f*100) + "%";
    if (f >= 0.999) { state.timelineDate = null; document.getElementById("tl-date").textContent = "all time"; }
    else {
      const t = new Date(dateMin.getTime() + f * (dateMax - dateMin));
      state.timelineDate = t;
      document.getElementById("tl-date").textContent = fmtDate(t);
    }
    rebuild();
  }
  setTimelineFraction(1);
  scrubber.addEventListener("click", e => {
    const r = scrubber.getBoundingClientRect();
    setTimelineFraction((e.clientX - r.left) / r.width);
  });
  let dragging = false;
  scrubber.addEventListener("mousedown", () => dragging = true);
  window.addEventListener("mouseup", () => dragging = false);
  window.addEventListener("mousemove", e => {
    if (!dragging) return;
    const r = scrubber.getBoundingClientRect();
    setTimelineFraction((e.clientX - r.left) / r.width);
  });
  document.getElementById("tl-reset").addEventListener("click", ()=>setTimelineFraction(1));
  document.getElementById("tl-play").addEventListener("click", function(){
    if (state.playing) {
      clearInterval(state.playTimer); state.playing = false;
      this.classList.remove("playing"); this.textContent = "▶ Play history";
      return;
    }
    state.playing = true; this.classList.add("playing"); this.textContent = "■ Stop";
    let f = 0; setTimelineFraction(f);
    state.playTimer = setInterval(() => {
      f += 0.025;
      if (f > 1.05) { clearInterval(state.playTimer); state.playing = false;
        this.classList.remove("playing"); this.textContent = "▶ Play history";
        setTimelineFraction(1); return; }
      setTimelineFraction(f);
    }, 220);
  });

  // ---------- THEME TOGGLE ----------
  document.querySelectorAll('[data-theme-set]').forEach(b => {
    b.addEventListener("click", () => {
      const v = b.dataset.themeSet;
      html.setAttribute("data-theme", v);
      document.querySelectorAll('[data-theme-set]').forEach(x => x.classList.toggle("active", x.dataset.themeSet === v));
      try { localStorage.setItem("ovp-theme", v); } catch(e){}
      rebuild(); // labels recolor
    });
  });
  try {
    const t = localStorage.getItem("ovp-theme");
    if (t) {
      html.setAttribute("data-theme", t);
      document.querySelectorAll('[data-theme-set]').forEach(x => x.classList.toggle("active", x.dataset.themeSet === t));
    }
  } catch(e){}

  // ---------- SPIN ----------
  let spinAngle = 0;
  setInterval(() => {
    if (state.spin === "off") return;
    spinAngle += 0.005;
    const r = 380;
    Graph.cameraPosition({ x: r*Math.sin(spinAngle), z: r*Math.cos(spinAngle) }, undefined, 0);
  }, 30);

  // ---------- KICK OFF ----------
  rebuild();
  document.getElementById("hud-comms").textContent = data.communities.length;
  // initial camera
  setTimeout(()=>Graph.cameraPosition({ x: 0, y: 0, z: 380 }, undefined, 1200), 200);
})();
