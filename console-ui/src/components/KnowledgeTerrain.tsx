/**
 * KnowledgeTerrain — an INTERACTIVE 3D "themescape" of the corpus (flomo-style).
 * Fetches `/api/terrain` (2D layout of themed packs, built by
 * `ovp2 crystal-terrain`), turns point density into a heightmapped terrain mesh
 * (contour-line shader), scatters each source as a glowing point on the surface,
 * floats theme labels on the peaks, and lets you ORBIT (drag), ZOOM (wheel), and
 * CLICK a point to open its source. A 2D/3D toggle tilts the camera top-down;
 * the timeline scrubber REPLAYS the terrain growing month by month. Its own dark
 * "night map" regardless of app theme.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n';
import { STATIC_MODE, terrainUrl } from '../lib/api';
import { activeClaims, themeRoute } from '../lib/derive';
import { useModel } from '../model';
import type { IndexModel } from '../lib/types';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
// Fat lines: plain THREE.Line ignores linewidth in WebGL (always 1px), so the
// evidence lines need Line2/LineMaterial to render at a real pixel width.
import { LineSegments2 } from 'three/examples/jsm/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/examples/jsm/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js';

// A rendered point is either a source (has `sha`, opens /library) or — in the
// claim perspective — a crystal (has `claim_id`, opens its theme page). The two
// share the same buffer/hover/label machinery; only the click target differs.
// A crystal also carries `sx`/`sy`: the LAYOUT positions of its cited sources,
// so hovering it can draw evidence lines out to them (they scatter across
// islands — that scatter IS the signal).
type TPoint = { id: string; sha: string; title: string; date: string; theme: string; theme_id: number; x: number; y: number; tags?: string[]; tags_inferred?: string[]; claim_id?: string; sx?: number[]; sy?: number[]; srcCount?: number; spanClusters?: number };
type TTheme = { id: number; label: string; label_zh: string; cx: number; cy: number; count: number };
type Terrain = {
  points: TPoint[];
  themes: TTheme[];
  bounds: [number, number, number, number];
  point_count: number;
};

const GOLDEN = 2.399963229728653; // radians — phyllotaxis angle, spreads indices

// What the mountain HEIGHT encodes (KMEM-style selectable relief). The land
// layout (x/y) is always semantic; only the z-metric changes.
type Metric = 'density' | 'recency' | 'influence';

/** Crystal overlay for the SOURCE terrain: each active claim marked at the
 * island of the theme its evidence most belongs to (the DOMINANT cluster among
 * its cited sources), NOT at the 2D centroid of those sources. Averaging
 * already-projected coordinates is meaningless on a non-linear layout — the mean
 * of scattered points lands in a semantic no-man's-land (often a different
 * theme's peak). Dominant-cluster placement instead sits the crystal on a real
 * evidence home; the evidence LINES (sx/sy) then reveal how far its support
 * reaches. Co-located crystals spiral out around their shared home via a
 * deterministic phyllotaxis so they don't stack. The land + theme labels stay
 * source-derived. Returns [] until the model loads; claims with no positioned
 * source are dropped. */
function buildCrystals(data: Terrain | null, model: IndexModel | null): TPoint[] {
  if (!data || !model) return [];
  // Join claim sources to terrain points by case_id — `p.id` IS the case_id
  // crystal-terrain emitted, and claims cite by case_id. A sha round-trip would
  // collapse packs that share a content sha and drop legacy packs with no sha.
  const posByCase = new Map(data.points.map((p) => [p.id, p]));
  const themeById = new Map(data.themes.map((th) => [th.id, th]));
  const [minx, miny, maxx, maxy] = data.bounds;
  const step = Math.max(maxx - minx, maxy - miny, 1e-6) * 0.012;
  // Per-home spiral counter so many crystals sharing one island fan out.
  const homeSeen = new Map<number, number>();

  const out: TPoint[] = [];
  activeClaims(model.claims).forEach((c) => {
    const srcPts = c.sources
      .map((caseId) => posByCase.get(caseId))
      .filter((p): p is TPoint => !!p);
    if (srcPts.length === 0) return;
    // Dominant cluster = the real theme id contributing the most sources.
    // Prefer a real theme (id >= 0) over the noise bucket on ties.
    const counts = new Map<number, number>();
    for (const p of srcPts) counts.set(p.theme_id, (counts.get(p.theme_id) ?? 0) + 1);
    let domId = srcPts[0].theme_id;
    let best = -1;
    for (const [tid, n] of counts) {
      if (n > best || (n === best && tid >= 0 && domId < 0)) {
        best = n;
        domId = tid;
      }
    }
    const home = themeById.get(domId);
    // Fall back to the source centroid only when the dominant cluster is noise
    // (no island center to anchor to).
    const baseX = home ? home.cx : srcPts.reduce((s, p) => s + p.x, 0) / srcPts.length;
    const baseY = home ? home.cy : srcPts.reduce((s, p) => s + p.y, 0) / srcPts.length;
    // Spiral only crystals that share a REAL island home. Noise-dominated
    // crystals have no shared anchor — each sits on its own evidence centroid, so
    // sharing one counter would push later ones ever further from their evidence
    // and off the land.
    let rr = 0;
    let ang = 0;
    if (home) {
      const k = homeSeen.get(domId) ?? 0;
      homeSeen.set(domId, k + 1);
      rr = step * Math.sqrt(k); // 0 for the first, growing spiral after
      ang = k * GOLDEN;
    }
    out.push({
      id: `claim:${c.claim_id}`,
      sha: '',
      claim_id: c.claim_id,
      title: c.claim,
      date: '',
      // `theme` is the ROUTE key only (click → themeRoute). Keep the claim's own
      // theme, '' for unthemed — never borrow the dominant CLUSTER label, or an
      // unthemed claim would open an unrelated theme page. The tooltip shows the
      // dominant cluster name separately via `theme_id`.
      theme: c.theme ?? '',
      theme_id: domId,
      x: baseX + Math.cos(ang) * rr,
      y: baseY + Math.sin(ang) * rr,
      sx: srcPts.map((p) => p.x),
      sy: srcPts.map((p) => p.y),
      srcCount: srcPts.length,
      spanClusters: counts.size,
    });
  });
  return out;
}

const SIZE = 220;
const GRID = 150;
const HEIGHT = 42;
const SIGMA = 3.4;
const FULL = '9999-99-99'; // cutoff meaning "everything"

// Community palette for coloring points by theme. The terrain is its own dark
// night map regardless of app theme, so this pins the DARK-theme values of the
// design system's --c-1..8 — reading the live CSS vars would tint points with
// light-theme inks on a dark map. Colors are assigned by theme size rank
// (biggest theme = first color), the same CONVENTION KnowledgeGraph uses for
// clusters. Note: the graph ranks themes by claim count and the terrain by
// source count, so the two views usually — not provably — agree per theme;
// exact cross-view color identity would need a shared theme→color projection.
const THEME_COLORS = ['#3b82f6', '#06b6d4', '#22c55e', '#eab308', '#a78bfa', '#f472b6', '#14b8a6', '#94a3b8'];
const NOISE_COLOR = '#66707c'; // unthemed/noise points — dim slate background
// Crystals are a distinct FOREGROUND layer over the source land: a warm gold
// that no theme swatch uses, so a marked claim never reads as just another
// source dot. Evidence lines share the hue at low alpha.
const CRYSTAL_COLOR = '#ffd76a';

function themeColorMap(themes: TTheme[]): Map<number, string> {
  const m = new Map<number, string>();
  [...themes]
    .sort((a, b) => b.count - a.count)
    .forEach((th, i) => m.set(th.id, THEME_COLORS[i % THEME_COLORS.length]));
  return m;
}

function glowTexture(): THREE.Texture {
  // 128px (was 64 → the points looked pixelated/mosaic when magnified) with a
  // BRIGHT SHARP core and a quick soft falloff, so each source reads as a crisp
  // bright dot rather than a fuzzy blob. NEUTRAL white ramp: the per-vertex
  // theme color multiplies this texture, so any blue tint here would shift
  // every warm theme color toward mud.
  const S = 128;
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d')!;
  const grd = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.14, 'rgba(255,255,255,0.98)');
  grd.addColorStop(0.4, 'rgba(255,255,255,0.4)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, S, S);
  const t = new THREE.CanvasTexture(c);
  t.needsUpdate = true;
  return t;
}

/** A four-point diamond/star sprite for crystals — a shape the round source
 * glow never takes, so crystals stay distinguishable even at the same color
 * rank. White ramp (the material color multiplies it). */
function crystalTexture(): THREE.Texture {
  const S = 128;
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d')!;
  g.translate(S / 2, S / 2);
  // Soft round core for a bright center.
  const core = g.createRadialGradient(0, 0, 0, 0, 0, S * 0.22);
  core.addColorStop(0, 'rgba(255,255,255,1)');
  core.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = core;
  g.beginPath();
  g.arc(0, 0, S * 0.22, 0, Math.PI * 2);
  g.fill();
  // Diamond body.
  const r = S * 0.46;
  const grd = g.createRadialGradient(0, 0, 0, 0, 0, r);
  grd.addColorStop(0, 'rgba(255,255,255,0.95)');
  grd.addColorStop(0.5, 'rgba(255,255,255,0.55)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.beginPath();
  g.moveTo(0, -r);
  g.lineTo(r * 0.62, 0);
  g.lineTo(0, r);
  g.lineTo(-r * 0.62, 0);
  g.closePath();
  g.fill();
  const t = new THREE.CanvasTexture(c);
  t.needsUpdate = true;
  return t;
}

export default function KnowledgeTerrain({
  height = 600,
  persp = 'source',
}: {
  height?: number;
  persp?: 'claim' | 'source';
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const { t, lang } = useI18n();
  const { model } = useModel();
  // Read inside the imperative three.js effect without making `lang` a build
  // dep (a scene rebuild on every locale toggle would reset the camera).
  const langRef = useRef(lang);
  langRef.current = lang;
  // Live handles to the floating theme labels so a locale toggle can rewrite
  // their text without rebuilding the WebGL scene.
  const labelHandlesRef = useRef<{ el: HTMLDivElement; th: TTheme }[]>([]);
  const [data, setData] = useState<Terrain | null>(null);
  // Flag, not a message — translate at render so a locale toggle doesn't retrigger
  // the fetch effect (which would replace `data` and rebuild the whole scene).
  const [failed, setFailed] = useState(false);
  const [webglFailed, setWebglFailed] = useState(false);
  const [mode, setMode] = useState<'3d' | '2d'>('3d');
  const modeRef = useRef(mode);
  modeRef.current = mode;
  // Selectable height metric — read imperatively so a change reshapes the land
  // without rebuilding the WebGL scene (like the timeline scrubber).
  const [metric, setMetric] = useState<Metric>('density');
  const metricRef = useRef(metric);
  metricRef.current = metric;
  const [hover, setHover] = useState<{ mx: number; my: number; p: TPoint } | null>(null);

  // The land, source points, theme labels, legend and timeline ALWAYS read the
  // source terrain `data` — that layer is semantically honest (peaks = dense
  // source communities). Crystals are an OVERLAY on top of that same landscape,
  // shown only in the claim perspective. `render` aliases `data` so the scene's
  // land/point machinery is unchanged.
  const render = data;
  // Stabilize the crystals reference across model polls. ModelProvider swaps
  // `model` every 12-60s, but the overlay only changes when a claim's
  // id/theme/sources change. Keying the memo on a content SIGNATURE (not the
  // model reference) keeps the WebGL scene — a build dep of `crystals` — from
  // tearing down and resetting the camera on every no-op poll, including in the
  // default source view where the overlay isn't even shown.
  const claimSig = useMemo(
    () =>
      model
        ? activeClaims(model.claims)
            // Include the claim TEXT: a rewrite can keep id/theme/sources yet
            // change what the tooltip must show, and claim_id can collide across
            // runs, so text is what makes the signature honest.
            .map((c) => `${c.claim_id}:${c.theme ?? ''}:${c.claim}:${c.sources.join(',')}`)
            .join('|')
        : '',
    [model],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const crystals = useMemo(() => buildCrystals(data, model), [data, claimSig]);
  const showCrystals = persp === 'claim';

  const months = useMemo(() => {
    if (!render) return [] as string[];
    const set = new Set<string>();
    for (const p of render.points) if (p.date) set.add(p.date.slice(0, 7));
    return [...set].sort();
  }, [render]);
  const [monthIdx, setMonthIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  // Theme/tag filters compose with the timeline cutoff; the terrain reshapes to
  // the filtered subset just like it does when scrubbing time. `selTag` is
  // ignored (not cleared) in the claim perspective — claims carry no tags, so
  // applying it would blank the map; it comes back when you return to sources.
  const [selTheme, setSelTheme] = useState<number | null>(null);
  const [selTag, setSelTag] = useState<string | null>(null);
  const applyFilterRef = useRef<
    ((cutoff: string, themeId: number | null, tag: string | null) => void) | null
  >(null);
  // Imperative "fly the camera to this theme's cluster" — set by the three.js
  // effect, called from the legend so you don't have to orbit around to find a
  // cluster.
  const focusThemeRef = useRef<((cx: number, cy: number) => void) | null>(null);

  useEffect(() => {
    let off = false;
    fetch(terrainUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('not built'))))
      .then((d: Terrain) => !off && setData(d))
      .catch(() => !off && setFailed(true));
    return () => {
      off = true;
    };
  }, []);

  // Default the scrubber to "all" once months are known.
  useEffect(() => {
    if (months.length) setMonthIdx(months.length - 1);
  }, [months]);

  // ---- three.js scene (built once per dataset) ----
  useEffect(() => {
    if (!render || !wrapRef.current) return;
    // Alias so the source/claim datasets share one imperative body verbatim.
    const data = render;
    const wrap = wrapRef.current;
    let W = wrap.clientWidth;
    const H = height;

    const [minx, miny, maxx, maxy] = data.bounds;
    // One uniform scale (max span) keeps the backend's aspect ratio — scaling
    // each axis to SIZE independently would stretch a 100×50 layout to 220×220,
    // distorting semantic angles/distances. Center the shorter axis.
    const spanMax = Math.max(maxx - minx, maxy - miny, 1e-6);
    const sx = (v: number) => ((v - minx) - (maxx - minx) / 2) / spanMax * SIZE;
    const sy = (v: number) => ((v - miny) - (maxy - miny) / 2) / spanMax * SIZE;

    // Separable Gaussian kernel for the KDE.
    const kern: number[] = [];
    const R = Math.ceil(SIGMA * 3);
    for (let i = -R; i <= R; i++) kern.push(Math.exp(-(i * i) / (2 * SIGMA * SIGMA)));
    const ksum = kern.reduce((a, b) => a + b, 0);
    // Zero-pad the separable Gaussian: SKIP out-of-range taps rather than
    // clamping to the border cell. Clamping re-samples an edge point once per
    // out-of-range tap (~22× inflation for a corner point), so it — not a real
    // cluster — would set fullMax and flatten every genuine peak.
    const blur = (src: Float32Array) => {
      const h = new Float32Array(GRID * GRID);
      for (let y = 0; y < GRID; y++)
        for (let x = 0; x < GRID; x++) {
          let s = 0;
          for (let k = -R; k <= R; k++) {
            const xx = x + k;
            if (xx >= 0 && xx < GRID) s += src[y * GRID + xx] * kern[k + R];
          }
          h[y * GRID + x] = s / ksum;
        }
      const o = new Float32Array(GRID * GRID);
      for (let y = 0; y < GRID; y++)
        for (let x = 0; x < GRID; x++) {
          let s = 0;
          for (let k = -R; k <= R; k++) {
            const yy = y + k;
            if (yy >= 0 && yy < GRID) s += h[yy * GRID + x] * kern[k + R];
          }
          o[y * GRID + x] = s / ksum;
        }
      return o;
    };
    // Weighted KDE: each source's contribution to the land height is scaled by
    // the ACTIVE metric (density=1, recency=newer higher, influence=how many
    // crystals cite it). `weightOf` maps a point to its weight.
    const densityFor = (pts: TPoint[], weightOf: (p: TPoint) => number) => {
      const grid = new Float32Array(GRID * GRID);
      for (const p of pts) {
        const w = weightOf(p);
        if (w <= 0) continue;
        const gx = Math.min(GRID - 1, Math.max(0, Math.round(((sx(p.x) / SIZE) + 0.5) * (GRID - 1))));
        const gy = Math.min(GRID - 1, Math.max(0, Math.round(((sy(p.y) / SIZE) + 0.5) * (GRID - 1))));
        grid[gy * GRID + gx] += w;
      }
      return blur(grid);
    };

    // ---- per-metric source weights ----
    // Recency: rank sources by date across the corpus's OWN range so we need no
    // "now"; newest ~1, oldest ~0.15, undated a low baseline. This is "growth".
    const dated = data.points.map((p) => p.date).filter(Boolean).sort();
    // Epoch DAYS, not YYYYMMDD-as-decimal: the latter makes 2025-12-31 and
    // 2026-01-01 read ~8870 apart despite being adjacent, warping the relief at
    // every month/year boundary.
    const asDay = (d: string) => {
      const t = Date.parse(d);
      return Number.isNaN(t) ? 0 : Math.floor(t / 86_400_000);
    };
    const lo = asDay(dated[0] ?? '');
    const hi = asDay(dated[dated.length - 1] ?? '');
    const recencyW = (p: TPoint) => {
      if (!p.date || hi <= lo) return 0.15;
      return 0.15 + 0.85 * ((asDay(p.date) - lo) / (hi - lo));
    };
    // Influence: how many active crystals cite each source. A source that feeds
    // many durable claims rises; uncited stays flat. A terrain point's `id` IS
    // the case_id that crystal-terrain emitted, and claims cite by case_id — so
    // key on `p.id` directly. (The old sha bridge zeroed legacy empty-sha points
    // and collapsed distinct case_ids that share one source sha.)
    const citeByCase = new Map<string, number>();
    if (model) {
      for (const c of activeClaims(model.claims)) {
        for (const caseId of c.sources) citeByCase.set(caseId, (citeByCase.get(caseId) ?? 0) + 1);
      }
    }
    const influenceW = (p: TPoint) => citeByCase.get(p.id) ?? 0;
    const weightOfFor = (metric: Metric): ((p: TPoint) => number) =>
      metric === 'recency' ? recencyW : metric === 'influence' ? influenceW : () => 1;

    // Fixed normalizer PER METRIC from the FULL corpus, cached, so the terrain
    // GROWS over the timeline instead of rescaling to full height each step and
    // each metric fills the height range on its own scale.
    const fullMaxCache = new Map<Metric, number>();
    const fullMaxFor = (metric: Metric): number => {
      let m = fullMaxCache.get(metric);
      if (m == null) {
        const dens = densityFor(data.points, weightOfFor(metric));
        m = 1e-6;
        for (const v of dens) m = Math.max(m, v);
        fullMaxCache.set(metric, m);
      }
      return m;
    };
    const heightFn = (dens: Float32Array, fullMax: number) => (wx: number, wz: number) => {
      const fx = ((wx / SIZE) + 0.5) * (GRID - 1);
      const fz = ((wz / SIZE) + 0.5) * (GRID - 1);
      const x0 = Math.min(GRID - 1, Math.max(0, Math.floor(fx)));
      const z0 = Math.min(GRID - 1, Math.max(0, Math.floor(fz)));
      const x1 = Math.min(GRID - 1, x0 + 1);
      const z1 = Math.min(GRID - 1, z0 + 1);
      const tx = fx - x0;
      const tz = fz - z0;
      const a = dens[z0 * GRID + x0];
      const b = dens[z0 * GRID + x1];
      const c = dens[z1 * GRID + x0];
      const d = dens[z1 * GRID + x1];
      const v = a * (1 - tx) * (1 - tz) + b * tx * (1 - tz) + c * (1 - tx) * tz + d * tx * tz;
      return (v / fullMax) * HEIGHT;
    };

    // ---- renderer / scene / camera ----
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#0d0f13');
    scene.fog = new THREE.Fog('#0d0f13', SIZE * 0.9, SIZE * 2.2);
    const camera = new THREE.PerspectiveCamera(45, W / H, 1, SIZE * 6);
    camera.position.set(0, HEIGHT * 3.2, SIZE * 0.82);

    // WebGLRenderer throws on clients where WebGL is unavailable/disabled
    // (GPU-off browsers, locked-down webviews). Catch it so selecting Terrain
    // shows a fallback message instead of unmounting the whole app.
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      setWebglFailed(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    renderer.domElement.style.borderRadius = '12px';
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.touchAction = 'none';
    wrap.style.overscrollBehavior = 'none';
    wrap.appendChild(renderer.domElement);

    const labelRenderer = new CSS2DRenderer();
    labelRenderer.setSize(W, H);
    // overflow:hidden clips the floating theme labels to the terrain box (some
    // project outside it behind the camera) WITHOUT clipping the React tooltip.
    labelRenderer.domElement.style.cssText =
      'position:absolute;top:0;left:0;width:100%;height:100%;overflow:hidden;pointer-events:none;';
    wrap.appendChild(labelRenderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.minDistance = 40;
    controls.maxDistance = SIZE * 1.8;
    controls.target.set(0, 0, 0);

    // ---- terrain mesh ----
    const geo = new THREE.PlaneGeometry(SIZE, SIZE, GRID - 1, GRID - 1);
    geo.rotateX(-Math.PI / 2);
    const gpos = geo.attributes.position as THREE.BufferAttribute;
    const terrainMat = new THREE.ShaderMaterial({
      uniforms: {
        uMaxH: { value: HEIGHT },
        // Muted slate contour line (was loud cyan #7fd6e6 → looked busy/cheap)
        // and a deeper, more neutral terrain gradient.
        uLine: { value: new THREE.Color('#3d6076') },
        uBase: { value: new THREE.Color('#0a0e13') },
        uPeak: { value: new THREE.Color('#16232f') },
      },
      vertexShader: `varying float vH; void main(){ vH=position.y; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
      fragmentShader: `
        uniform float uMaxH; uniform vec3 uLine; uniform vec3 uBase; uniform vec3 uPeak; varying float vH;
        void main(){
          float h=clamp(vH/uMaxH,0.0,1.0);
          // Fewer levels (16, was 26) and a MUCH subtler line so the terrain is a
          // quiet backdrop and the glowing source points read as the foreground.
          float g=h*16.0; float fp=fract(g); float dist=min(fp,1.0-fp);
          float aa=fwidth(g)*1.3+1e-4; float edge=1.0-smoothstep(0.0,aa,dist);
          vec3 terrain=mix(uBase,uPeak,h);
          gl_FragColor=vec4(mix(terrain,uLine,edge*(0.18+0.42*h)),1.0);
        }`,
    });
    scene.add(new THREE.Mesh(geo, terrainMat));

    // ---- points (full-size buffer, drawn up to the visible count) ----
    const colorByTheme = new Map<number, THREE.Color>();
    for (const [id, hex] of themeColorMap(data.themes)) colorByTheme.set(id, new THREE.Color(hex));
    const noiseColor = new THREE.Color(NOISE_COLOR);
    const pgeo = new THREE.BufferGeometry();
    const parr = new Float32Array(data.points.length * 3);
    const carr = new Float32Array(data.points.length * 3);
    pgeo.setAttribute('position', new THREE.BufferAttribute(parr, 3));
    pgeo.setAttribute('color', new THREE.BufferAttribute(carr, 3));
    const pmat = new THREE.PointsMaterial({
      size: 3.8, map: glowTexture(), transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, color: 0xffffff, sizeAttenuation: true,
      vertexColors: true,
    });
    const points = new THREE.Points(pgeo, pmat);
    points.frustumCulled = false;
    scene.add(points);
    const visibleIdx: number[] = []; // geometry slot -> data.points index

    const mark = new THREE.Points(
      new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(3), 3)),
      new THREE.PointsMaterial({ size: 9, map: glowTexture(), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, color: 0xffffff }),
    );
    mark.visible = false;
    scene.add(mark);

    // ---- crystal overlay (claim perspective only) ----
    // A gold diamond layer floating over the source land. Each crystal sits at
    // its dominant-cluster home; hovering it draws evidence lines to its cited
    // sources (which scatter across islands). Built lazily — an empty layer when
    // there are no crystals or we're in source mode costs nothing.
    const CRYSTAL_LIFT = 4; // float above the source dots so they read as markers
    const cgeo = new THREE.BufferGeometry();
    const cparr = new Float32Array(Math.max(crystals.length, 1) * 3);
    cgeo.setAttribute('position', new THREE.BufferAttribute(cparr, 3));
    // Resting crystals are SMALL and dim — anchors, not the story. The story is
    // the relationship, which lights up on hover (bigger mark + bright thick
    // lines). A field of hundreds of loud diamonds would drown both the land and
    // the lines, which is exactly the "markers too big, lines invisible" failure.
    const cmat = new THREE.PointsMaterial({
      size: 4, map: crystalTexture(), transparent: true, opacity: 0.6, depthWrite: false,
      blending: THREE.AdditiveBlending, color: new THREE.Color(CRYSTAL_COLOR),
      sizeAttenuation: true,
    });
    const cpoints = new THREE.Points(cgeo, cmat);
    cpoints.frustumCulled = false;
    cpoints.visible = showCrystals;
    scene.add(cpoints);
    const cVisibleIdx: number[] = []; // geometry slot -> crystals index

    // Highlight sprite for the hovered crystal (the only bright diamond).
    const cmark = new THREE.Points(
      new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(3), 3)),
      new THREE.PointsMaterial({ size: 11, map: crystalTexture(), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, color: new THREE.Color(CRYSTAL_COLOR) }),
    );
    cmark.visible = false;
    // Never cull: the cached boundingSphere is from the marker's FIRST position,
    // so after moving it (copyArray) a distant camera could wrongly cull the
    // highlight. The single sprite is trivial to always-draw.
    cmark.frustumCulled = false;
    scene.add(cmark);

    // Evidence lines: fat Line2 segments rewritten to the hovered crystal's
    // crystal→source pairs. Bright + ~3px so the relationship is the salient
    // thing, not the anchor. Capped so a pathological claim can't blow the
    // buffer; the tooltip still reports the true source count.
    const LINE_CAP = 64;
    const larr = new Float32Array(LINE_CAP * 2 * 3);
    const lgeo = new LineSegmentsGeometry();
    const lmat = new LineMaterial({
      color: new THREE.Color(CRYSTAL_COLOR).getHex(),
      linewidth: 3, // pixels (worldUnits off)
      transparent: true, opacity: 0.95, depthWrite: false, depthTest: false,
    });
    lmat.resolution.set(W, H);
    const elines = new LineSegments2(lgeo, lmat);
    elines.frustumCulled = false;
    elines.visible = false;
    elines.renderOrder = 3; // draw over the land + points
    scene.add(elines);
    // Current land-height sampler, refreshed by applyFilter so crystal + line
    // endpoints sit on the land at the active timeline cutoff.
    let hAtCurrent: (x: number, z: number) => number = () => 0;

    // ---- theme labels ----
    const labelObjs = data.themes
      .slice()
      .sort((a, b) => b.count - a.count)
      .slice(0, 16)
      .map((th) => {
        const el = document.createElement('div');
        el.textContent = langRef.current === 'zh' ? th.label_zh : th.label;
        el.style.cssText =
          'color:rgba(233,230,224,0.94);font:600 12px system-ui;text-shadow:0 1px 3px rgba(0,0,0,0.85);white-space:nowrap;';
        const obj = new CSS2DObject(el);
        scene.add(obj);
        return { th, el, obj };
      });
    labelHandlesRef.current = labelObjs.map((l) => ({ el: l.el, th: l.th }));

    // Interaction state, hoisted so `applyCutoff` can reset a stale hover when
    // the visible set shrinks under it.
    let hoverSlot = -1;
    // Which crystal's evidence lines are currently drawn — so hovering the same
    // crystal across many pointermoves doesn't rebuild the line buffer each time
    // (setPositions reallocates interleaved attributes). -1 = none.
    let drawnCrystalIdx = -1;
    let downX = 0;
    let downY = 0;

    // ---- rebuild terrain/points/labels for a date cutoff + theme/tag filter ----
    const applyFilter = (cutoff: string, themeId: number | null, tag: string | null) => {
      const vis = data.points.filter(
        (p) =>
          (!p.date || p.date <= cutoff) &&
          (themeId == null || p.theme_id === themeId) &&
          (tag == null || (p.tags ?? []).includes(tag) || (p.tags_inferred ?? []).includes(tag)),
      );
      const metric = metricRef.current;
      const dens = densityFor(vis, weightOfFor(metric));
      const hAt = heightFn(dens, fullMaxFor(metric));
      hAtCurrent = hAt;
      for (let i = 0; i < gpos.count; i++) gpos.setY(i, hAt(gpos.getX(i), gpos.getZ(i)));
      gpos.needsUpdate = true;
      geo.computeVertexNormals();

      // In claim mode the sources recede to context so the gold crystals read as
      // the foreground; in source mode they stay full-strength.
      const srcDim = showCrystals ? 0.4 : 1;
      visibleIdx.length = 0;
      vis.forEach((p) => {
        const wx = sx(p.x);
        const wz = sy(p.y);
        const slot = visibleIdx.length;
        parr[slot * 3] = wx;
        parr[slot * 3 + 1] = hAt(wx, wz) + 1.6;
        parr[slot * 3 + 2] = wz;
        const col = colorByTheme.get(p.theme_id) ?? noiseColor;
        carr[slot * 3] = col.r * srcDim;
        carr[slot * 3 + 1] = col.g * srcDim;
        carr[slot * 3 + 2] = col.b * srcDim;
        visibleIdx.push(data.points.indexOf(p));
      });
      pgeo.setDrawRange(0, vis.length);
      pgeo.attributes.position.needsUpdate = true;
      pgeo.attributes.color.needsUpdate = true;
      // needsUpdate does NOT invalidate the cached bounding sphere the
      // raycaster culls against — after widening a filter, points outside the
      // stale sphere would be unhoverable. Null it so three recomputes lazily.
      pgeo.boundingSphere = null;

      // The hovered/marked point may have just dropped out of the visible set;
      // clear it so `onClick` can't read a now-out-of-range `visibleIdx` slot.
      hoverSlot = -1;
      mark.visible = false;
      setHover(null);

      // Key on community id, not the display label — two communities may share
      // a label, and keying on the string would light both anchors when only
      // one has visible points.
      const seen = new Set(vis.map((p) => p.theme_id));
      for (const l of labelObjs) {
        if (seen.has(l.th.id)) {
          l.el.style.display = '';
          const wx = sx(l.th.cx);
          const wz = sy(l.th.cy);
          l.obj.position.set(wx, hAt(wx, wz) + 6, wz);
        } else {
          l.el.style.display = 'none';
        }
      }

      // ---- crystals: place the gold markers on the current land surface ----
      // Respect only the theme filter (crystals have no date/tags); a crystal
      // shows when its DOMINANT cluster matches the selected theme. A hovered
      // crystal's lines are cleared here since the map just reshaped under it.
      cVisibleIdx.length = 0;
      if (showCrystals) {
        crystals.forEach((cr, ci) => {
          if (themeId != null && cr.theme_id !== themeId) return;
          const wx = sx(cr.x);
          const wz = sy(cr.y);
          const slot = cVisibleIdx.length;
          cparr[slot * 3] = wx;
          cparr[slot * 3 + 1] = hAt(wx, wz) + CRYSTAL_LIFT;
          cparr[slot * 3 + 2] = wz;
          cVisibleIdx.push(ci);
        });
      }
      cgeo.setDrawRange(0, cVisibleIdx.length);
      cgeo.attributes.position.needsUpdate = true;
      cgeo.boundingSphere = null;
      cmark.visible = false;
      elines.visible = false;
      drawnCrystalIdx = -1; // land reshaped; force a redraw on next hover
    };
    // Scene rebuilds (persp/dataset change) re-apply the CURRENT UI filters —
    // the driving effect below also fires post-mount with the real cutoff.
    applyFilter(FULL, selTheme, persp === 'claim' ? null : selTag);
    applyFilterRef.current = applyFilter;

    // ---- interaction (click-vs-drag) ----
    const ray = new THREE.Raycaster();
    ray.params.Points = { threshold: 2.4 };
    const ndc = new THREE.Vector2();
    // Raycast a visible-point slot from canvas-relative coords (offsetX/Y are
    // reflow-free — no getBoundingClientRect layout thrash). Returns -1 on miss.
    const slotAt = (offX: number, offY: number): number => {
      ndc.x = (offX / W) * 2 - 1;
      ndc.y = -(offY / H) * 2 + 1;
      ray.setFromCamera(ndc, camera);
      const hits = ray.intersectObject(points, false);
      const hit = hits.find((h) => (h.index ?? -1) < visibleIdx.length);
      return hit ? hit.index ?? -1 : -1;
    };
    // Crystal raycast (claim mode). Separate object + threshold so the bigger
    // gold markers are easy to grab.
    const cSlotAt = (offX: number, offY: number): number => {
      ndc.x = (offX / W) * 2 - 1;
      ndc.y = -(offY / H) * 2 + 1;
      ray.setFromCamera(ndc, camera);
      const hits = ray.intersectObject(cpoints, false);
      const hit = hits.find((h) => (h.index ?? -1) < cVisibleIdx.length);
      return hit ? hit.index ?? -1 : -1;
    };
    // Draw the hovered crystal's evidence lines: crystal → each cited source, on
    // the current land surface. Returns the source count actually drawn.
    const drawEvidenceLines = (cr: TPoint, cslot: number) => {
      const cx0 = cparr[cslot * 3];
      const cy0 = cparr[cslot * 3 + 1];
      const cz0 = cparr[cslot * 3 + 2];
      const n = Math.min(cr.sx?.length ?? 0, LINE_CAP);
      for (let j = 0; j < n; j++) {
        const wx = sx(cr.sx![j]);
        const wz = sy(cr.sy![j]);
        larr[j * 6] = cx0;
        larr[j * 6 + 1] = cy0;
        larr[j * 6 + 2] = cz0;
        larr[j * 6 + 3] = wx;
        larr[j * 6 + 4] = hAtCurrent(wx, wz) + 1.6;
        larr[j * 6 + 5] = wz;
      }
      if (n > 0) lgeo.setPositions(larr.subarray(0, n * 6));
      elines.visible = n > 0;
    };
    const pick = (ev: PointerEvent) => {
      // Claim mode: crystals are the interactive foreground; sources are context.
      if (showCrystals) {
        const cslot = cSlotAt(ev.offsetX, ev.offsetY);
        if (cslot >= 0) {
          const ci = cVisibleIdx[cslot];
          const cr = crystals[ci];
          cmark.visible = true;
          (cmark.geometry.attributes.position as THREE.BufferAttribute).copyArray([
            cparr[cslot * 3], cparr[cslot * 3 + 1], cparr[cslot * 3 + 2],
          ]);
          cmark.geometry.attributes.position.needsUpdate = true;
          // Only rebuild the line buffer when the hovered crystal changes — the
          // land is static between reshapes, so the lines don't move.
          if (ci !== drawnCrystalIdx) {
            drawEvidenceLines(cr, cslot);
            drawnCrystalIdx = ci;
          }
          setHover({ mx: ev.offsetX, my: ev.offsetY, p: cr });
          renderer.domElement.style.cursor = 'pointer';
        } else {
          cmark.visible = false;
          elines.visible = false;
          drawnCrystalIdx = -1;
          setHover(null);
          renderer.domElement.style.cursor = 'grab';
        }
        return;
      }
      hoverSlot = slotAt(ev.offsetX, ev.offsetY);
      if (hoverSlot >= 0) {
        const p = data.points[visibleIdx[hoverSlot]];
        mark.visible = true;
        (mark.geometry.attributes.position as THREE.BufferAttribute).copyArray([
          parr[hoverSlot * 3], parr[hoverSlot * 3 + 1], parr[hoverSlot * 3 + 2],
        ]);
        mark.geometry.attributes.position.needsUpdate = true;
        setHover({ mx: ev.offsetX, my: ev.offsetY, p });
        renderer.domElement.style.cursor = 'pointer';
      } else {
        mark.visible = false;
        setHover(null);
        renderer.domElement.style.cursor = 'grab';
      }
    };
    const onDown = (e: PointerEvent) => {
      downX = e.clientX;
      downY = e.clientY;
    };
    const onClick = (e: MouseEvent) => {
      if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) return; // a drag, not a click
      // Raycast AT the click/tap point — a touch tap (or a click after wheel
      // zoom) never fires pointermove, so hoverSlot would be stale or unset.
      let p: TPoint | null = null;
      if (showCrystals) {
        const cslot = cSlotAt(e.offsetX, e.offsetY);
        if (cslot >= 0 && cslot < cVisibleIdx.length) p = crystals[cVisibleIdx[cslot]];
      } else {
        const slot = slotAt(e.offsetX, e.offsetY);
        if (slot >= 0 && slot < visibleIdx.length) p = data.points[visibleIdx[slot]];
      }
      if (!p) return;
      // Crystals open their theme page; source points open /library. Both in a
      // NEW tab so the operator's tuned camera/timeline state survives (in-app
      // navigation would unmount the terrain and reset it).
      const path = p.claim_id
        ? themeRoute(p.theme)
        : p.sha
          ? `/library/${encodeURIComponent(p.sha)}`
          : null;
      if (!path) return;
      window.open(STATIC_MODE ? `#${path}` : path, '_blank', 'noopener');
    };
    renderer.domElement.addEventListener('pointermove', pick);
    renderer.domElement.addEventListener('pointerdown', onDown);
    renderer.domElement.addEventListener('click', onClick);

    // ---- legend-driven camera focus: fly to a theme's cluster centroid ----
    let focusCam: THREE.Vector3 | null = null;
    let focusTarget: THREE.Vector3 | null = null;
    const focusTheme = (cx: number, cy: number) => {
      const wx = sx(cx);
      const wz = sy(cy);
      focusTarget = new THREE.Vector3(wx, HEIGHT * 0.3, wz);
      focusCam = new THREE.Vector3(wx, HEIGHT * 1.7, wz + SIZE * 0.3);
    };
    focusThemeRef.current = focusTheme;

    // ---- loop (tween camera on 2D/3D switch or a legend focus) ----
    let raf = 0;
    const t3d = new THREE.Vector3(0, HEIGHT * 3.2, SIZE * 0.82);
    const t2d = new THREE.Vector3(0, SIZE * 1.15, 0.001);
    let appliedMode: '2d' | '3d' = '3d';
    let tweening = false;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      // A change from the applied mode STARTS a tween; once tweening we drive it
      // to completion off the CURRENTLY requested mode. Reversing 3D→2D→3D mid-
      // tween returns to appliedMode, so the start test is false — but tweening
      // is already true, and re-syncing the limit + target every frame from
      // modeRef inside the block below still lands the camera correctly. Setting
      // the limit only on the start test would strand it at the 2D clamp.
      if (modeRef.current !== appliedMode) tweening = true;
      if (tweening) {
        const want3d = modeRef.current === '3d';
        controls.enabled = false;
        controls.maxPolarAngle = want3d ? Math.PI * 0.49 : 0.35;
        const want = want3d ? t3d : t2d;
        camera.position.lerp(want, 0.12);
        camera.lookAt(controls.target);
        if (camera.position.distanceTo(want) < 1.5) {
          camera.position.copy(want);
          tweening = false;
          appliedMode = modeRef.current;
          controls.enabled = true;
        }
      } else if (focusCam && focusTarget) {
        // Fly to the picked cluster; re-enable orbit once we arrive so you can
        // look around it.
        controls.enabled = false;
        camera.position.lerp(focusCam, 0.12);
        controls.target.lerp(focusTarget, 0.12);
        if (camera.position.distanceTo(focusCam) < 1.5) {
          focusCam = null;
          focusTarget = null;
          controls.enabled = true;
        }
      }
      controls.update();
      renderer.render(scene, camera);
      labelRenderer.render(scene, camera);
    };
    loop();

    const ro = new ResizeObserver(() => {
      W = wrap.clientWidth;
      camera.aspect = W / H;
      camera.updateProjectionMatrix();
      renderer.setSize(W, H);
      labelRenderer.setSize(W, H);
      lmat.resolution.set(W, H); // fat lines scale in screen space
    });
    ro.observe(wrap);

    return () => {
      applyFilterRef.current = null;
      focusThemeRef.current = null;
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener('pointermove', pick);
      renderer.domElement.removeEventListener('pointerdown', onDown);
      renderer.domElement.removeEventListener('click', onClick);
      controls.dispose();
      geo.dispose();
      terrainMat.dispose();
      pgeo.dispose();
      // material.dispose() does NOT release its texture map — each glowTexture()
      // is a separate GPU resource. Dispose points + mark geometry/material/map
      // so remounts (List/Graph/Terrain tab switches) don't leak.
      pmat.map?.dispose();
      pmat.dispose();
      mark.geometry.dispose();
      const markMat = mark.material as THREE.PointsMaterial;
      markMat.map?.dispose();
      markMat.dispose();
      // Crystal overlay: geometry + material + diamond textures + evidence lines.
      cgeo.dispose();
      cmat.map?.dispose();
      cmat.dispose();
      cmark.geometry.dispose();
      const cmarkMat = cmark.material as THREE.PointsMaterial;
      cmarkMat.map?.dispose();
      cmarkMat.dispose();
      lgeo.dispose();
      lmat.dispose();
      renderer.dispose();
      wrap.removeChild(renderer.domElement);
      wrap.removeChild(labelRenderer.domElement);
      labelObjs.forEach((l) => l.obj.removeFromParent());
      labelHandlesRef.current = [];
    };
    // `crystals`/`showCrystals` are build deps: toggling the perspective must
    // rebuild the scene so the overlay layer + its interaction bind to the
    // current state (the land itself, from `render`, is perspective-invariant).
  }, [render, crystals, showCrystals, height]);

  // ---- drive the terrain from the scrubber ----
  const atLatest = monthIdx >= months.length - 1;
  const cutoff = atLatest || !months.length ? FULL : `${months[monthIdx]}-31`;
  // Theme ids with at least one point visible at the current timeline cutoff —
  // so the legend doesn't offer a future-only theme that would fly the camera
  // to an empty spot.
  const activeThemeIds = useMemo(() => {
    const s = new Set<number>();
    for (const p of render?.points ?? []) {
      if (!p.date || p.date <= cutoff) s.add(p.theme_id);
    }
    return s;
  }, [render, cutoff]);
  // A dataset switch (e.g. claim ⇄ source perspective on divergent data) can
  // drop the selected theme entirely; clear the selection rather than leaving
  // an empty terrain whose filter has no visible control to undo it.
  useEffect(() => {
    if (selTheme != null && render && !render.themes.some((th) => th.id === selTheme)) {
      setSelTheme(null);
    }
  }, [render, selTheme]);

  useEffect(() => {
    // No months guard: theme/tag filters must apply even on an undated corpus
    // (months empty → atLatest → FULL cutoff).
    applyFilterRef.current?.(
      atLatest || !months.length ? FULL : `${months[monthIdx]}-31`,
      selTheme,
      persp === 'claim' ? null : selTag,
    );
    // `crystals`/`showCrystals` included: a model poll can rebuild the scene
    // (which re-inits at FULL), so reapply the current cutoff or the slider and
    // terrain would silently disagree.
  }, [monthIdx, months, atLatest, selTheme, selTag, persp, metric, crystals, showCrystals]);

  // ---- play ----
  useEffect(() => {
    if (!playing || !months.length) return;
    const id = window.setInterval(() => {
      setMonthIdx((i) => {
        if (i >= months.length - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, 700);
    return () => window.clearInterval(id);
  }, [playing, months]);

  // ---- relabel the floating theme labels when the locale changes ----
  useEffect(() => {
    for (const l of labelHandlesRef.current) {
      l.el.textContent = lang === 'zh' ? l.th.label_zh : l.th.label;
    }
  }, [lang, render]);

  // Theme lookup for the localized tooltip (point carries only its community id).
  const themeById = useMemo(
    () => new Map((render?.themes ?? []).map((th) => [th.id, th] as const)),
    [render],
  );
  // Same rank→color assignment the scene uses, for legend swatches + tooltip.
  const themeColors = useMemo(() => themeColorMap(render?.themes ?? []), [render]);
  // Top tags across the rendered points, for the filter chips. A tag that only
  // ever appears machine-inferred keeps the portal's "weak" treatment (~#tag,
  // dashed). Claim points carry no tags → no chips in that perspective.
  // Legend rows: themes with visible points at the timeline cutoff, top-16 by
  // size — but the SELECTED theme always keeps its row, even when the cutoff
  // (or the cap) would drop it. Otherwise scrubbing to before the theme's
  // first point leaves an empty terrain with no control to clear the filter.
  const legendThemes = useMemo(() => {
    if (!render) return [] as TTheme[];
    const list = [...render.themes]
      .filter((th) => activeThemeIds.has(th.id))
      .sort((a, b) => b.count - a.count)
      .slice(0, 16);
    if (selTheme != null && !list.some((th) => th.id === selTheme)) {
      const sel = render.themes.find((th) => th.id === selTheme);
      if (sel) list.push(sel);
    }
    return list;
  }, [render, activeThemeIds, selTheme]);
  const tagChips = useMemo(() => {
    const counts = new Map<string, { n: number; inferredOnly: boolean }>();
    for (const p of render?.points ?? []) {
      for (const tg of p.tags ?? []) {
        const e = counts.get(tg) ?? { n: 0, inferredOnly: false };
        e.n += 1;
        e.inferredOnly = false;
        counts.set(tg, e);
      }
      for (const tg of p.tags_inferred ?? []) {
        const e = counts.get(tg) ?? { n: 0, inferredOnly: true };
        e.n += 1;
        counts.set(tg, e);
      }
    }
    return [...counts.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, 12);
  }, [render]);

  if (failed)
    return (
      <div className="graph-caption" style={{ padding: '2rem 0' }}>
        {t('knowledge.terrainNotBuilt')}
      </div>
    );
  if (webglFailed)
    return (
      <div className="graph-caption" style={{ padding: '2rem 0' }}>
        {t('knowledge.terrainNoWebgl')}
      </div>
    );

  return (
    <div
      ref={wrapRef}
      style={{ position: 'relative', width: '100%', height }}
    >
      <div style={{ position: 'absolute', top: 10, left: 12, zIndex: 2, color: 'rgba(233,230,224,0.6)', font: '12px system-ui', pointerEvents: 'none' }}>
        {render
          ? showCrystals
            ? t('knowledge.terrainHudClaims', { crystals: crystals.length, themes: render.themes.length })
            : t('knowledge.terrainHud', { notes: render.point_count, themes: render.themes.length })
          : t('knowledge.terrainLoading')}
      </div>
      {/* Clickable theme list — pick a cluster to FILTER the terrain to it and
          fly the camera there (click again to clear). Swatches carry the same
          rank-assigned community colors as the points. */}
      {render && render.themes.length > 0 && (
        <div
          className="terrain-legend"
          style={{
            position: 'absolute', top: 34, left: 12, zIndex: 2,
            maxHeight: Math.max(120, height - 96), overflowY: 'auto',
            display: 'flex', flexDirection: 'column', gap: 1, maxWidth: '44%',
            background: 'rgba(14,18,24,0.5)', borderRadius: 8, padding: '5px 6px',
          }}
        >
          {legendThemes.map((th) => {
              const on = selTheme === th.id;
              return (
                <button
                  key={th.id}
                  type="button"
                  title={t('knowledge.terrainFocusTheme')}
                  onClick={() => {
                    if (on) {
                      setSelTheme(null);
                      return;
                    }
                    setSelTheme(th.id);
                    if (mode === '2d') setMode('3d');
                    focusThemeRef.current?.(th.cx, th.cy);
                  }}
                  style={{
                    cursor: 'pointer', textAlign: 'left', border: 'none',
                    background: on ? 'rgba(127,214,230,0.16)' : 'none',
                    color: on ? '#e9f4f8' : 'rgba(233,230,224,0.82)',
                    font: '11px system-ui', padding: '2px 5px',
                    borderRadius: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}
                >
                  <span
                    style={{
                      display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
                      background: themeColors.get(th.id) ?? NOISE_COLOR,
                      marginRight: 5, verticalAlign: 'middle',
                    }}
                  />
                  {(lang === 'zh' ? th.label_zh : th.label) || th.label}
                  <span style={{ opacity: 0.45 }}> · {th.count}</span>
                </button>
              );
            })}
        </div>
      )}
      <div style={{ position: 'absolute', top: 10, right: 12, zIndex: 2, display: 'flex', gap: 8, alignItems: 'center' }}>
        {/* Height metric — what the mountains ENCODE. The x/y layout is fixed
            (semantic); this only reshapes z. */}
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value as Metric)}
          title={t('knowledge.terrainHeightBy')}
          style={{ cursor: 'pointer', background: 'rgba(20,24,30,0.9)', color: '#e9e6e0', border: '1px solid rgba(120,180,205,0.4)', borderRadius: 8, padding: '5px 8px', font: '12px system-ui' }}
        >
          <option value="density">{t('knowledge.terrainHeightDensity')}</option>
          <option value="recency">{t('knowledge.terrainHeightRecency')}</option>
          <option value="influence">{t('knowledge.terrainHeightInfluence')}</option>
        </select>
        <button
          type="button"
          onClick={() => setMode((m) => (m === '3d' ? '2d' : '3d'))}
          style={{ cursor: 'pointer', background: 'rgba(20,24,30,0.9)', color: '#e9e6e0', border: '1px solid rgba(120,180,205,0.4)', borderRadius: 8, padding: '5px 12px', font: '12px system-ui' }}
        >
          {mode === '3d' ? '2D' : '3D'}
        </button>
      </div>

      {/* Tag filter chips — single-select over the top tags of the rendered
          points; sits above the timeline bar when that is present. */}
      {persp === 'source' && tagChips.length > 0 && (
        <div
          style={{
            position: 'absolute', bottom: months.length > 1 ? 58 : 12, left: 16, right: 16,
            zIndex: 2, display: 'flex', flexWrap: 'wrap', gap: 6,
          }}
        >
          {tagChips.map(([tg, info]) => {
            const on = selTag === tg;
            return (
              <button
                key={tg}
                type="button"
                title={t('knowledge.terrainTagFilter')}
                onClick={() => setSelTag(on ? null : tg)}
                style={{
                  cursor: 'pointer', font: '11px system-ui', padding: '3px 9px', borderRadius: 999,
                  background: on ? 'rgba(127,214,230,0.18)' : 'rgba(16,20,26,0.8)',
                  color: on ? '#bfe9f2' : 'rgba(233,230,224,0.75)',
                  border: `1px ${info.inferredOnly ? 'dashed' : 'solid'} rgba(120,180,205,${on ? 0.55 : 0.28})`,
                }}
              >
                {info.inferredOnly ? `~#${tg}` : `#${tg}`}
                <span style={{ opacity: 0.5 }}> {info.n}</span>
              </button>
            );
          })}
        </div>
      )}

      {months.length > 1 && (
        <div
          style={{
            position: 'absolute', bottom: 12, left: 16, right: 16, zIndex: 2,
            display: 'flex', alignItems: 'center', gap: 12,
            background: 'rgba(16,20,26,0.82)', border: '1px solid rgba(120,180,205,0.25)',
            borderRadius: 10, padding: '7px 12px',
          }}
        >
          <button
            type="button"
            onClick={() => {
              if (atLatest) setMonthIdx(0);
              setPlaying((p) => !p);
            }}
            style={{ cursor: 'pointer', background: 'none', border: 'none', color: '#7fd6e6', font: '13px system-ui' }}
            aria-label={playing ? t('knowledge.terrainPause') : t('knowledge.terrainPlay')}
          >
            {playing ? '⏸' : '▶'}
          </button>
          <input
            type="range"
            min={0}
            max={months.length - 1}
            value={monthIdx}
            onChange={(e) => {
              setPlaying(false);
              setMonthIdx(Number(e.target.value));
            }}
            style={{ flex: 1, accentColor: '#7fd6e6' }}
          />
          <span style={{ color: 'rgba(233,230,224,0.85)', font: '12px system-ui', minWidth: 92, textAlign: 'right' }}>
            {atLatest ? t('knowledge.terrainAllTime') : months[monthIdx]}
          </span>
        </div>
      )}

      {hover && (
        <div
          style={{
            position: 'absolute', zIndex: 3, pointerEvents: 'none',
            left: Math.min(hover.mx + 14, (wrapRef.current?.clientWidth ?? 0) - 250),
            top: hover.my + 14, maxWidth: 250,
            background: 'rgba(18,22,28,0.96)', border: '1px solid rgba(120,180,205,0.4)',
            borderRadius: 8, padding: '8px 10px', color: '#e9e6e0', font: '12px system-ui',
            boxShadow: '0 8px 26px rgba(0,0,0,0.55)',
          }}
        >
          <div
            style={{
              // Crystals get the gold header (their dominant theme), sources the
              // theme-rank color.
              color: hover.p.claim_id
                ? CRYSTAL_COLOR
                : themeColors.get(hover.p.theme_id) ?? '#7fd6e6',
              fontSize: 11, marginBottom: 3,
            }}
          >
            {(() => {
              const th = themeById.get(hover.p.theme_id);
              // Noise packs (theme_id < 0) are omitted from `themes`, so localize
              // the unclassified label rather than falling back to English.
              const name = th
                ? (lang === 'zh' ? th.label_zh : th.label)
                : t('knowledge.terrainUnclassified');
              const prefix = hover.p.claim_id ? `◆ ${t('knowledge.terrainCrystalTag')} · ` : '';
              return `${prefix}${name}${hover.p.date ? ` · ${hover.p.date}` : ''}`;
            })()}
          </div>
          <div style={{ lineHeight: 1.35 }}>{hover.p.title}</div>
          {hover.p.claim_id && (hover.p.srcCount ?? 0) > 0 && (
            <div style={{ marginTop: 4, color: CRYSTAL_COLOR, opacity: 0.85, fontSize: 11 }}>
              {t('knowledge.terrainCrystalEvidence', {
                n: hover.p.srcCount ?? 0,
                m: hover.p.spanClusters ?? 0,
              })}
            </div>
          )}
          {((hover.p.tags?.length ?? 0) > 0 || (hover.p.tags_inferred?.length ?? 0) > 0) && (
            <div style={{ marginTop: 4, color: 'rgba(233,230,224,0.55)', fontSize: 11 }}>
              {[
                ...(hover.p.tags ?? []).map((tg) => `#${tg}`),
                ...(hover.p.tags_inferred ?? []).map((tg) => `~#${tg}`),
              ]
                .slice(0, 4)
                .join(' ')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
