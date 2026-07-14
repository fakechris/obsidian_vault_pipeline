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
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';

type TPoint = { id: string; sha: string; title: string; date: string; theme: string; x: number; y: number };
type TTheme = { id: number; label: string; cx: number; cy: number; count: number };
type Terrain = {
  points: TPoint[];
  themes: TTheme[];
  bounds: [number, number, number, number];
  point_count: number;
};

const SIZE = 220;
const GRID = 150;
const HEIGHT = 42;
const SIGMA = 3.4;
const FULL = '9999-99-99'; // cutoff meaning "everything"

function glowTexture(): THREE.Texture {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d')!;
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, 'rgba(180,225,245,1)');
  grd.addColorStop(0.25, 'rgba(130,200,230,0.85)');
  grd.addColorStop(1, 'rgba(130,200,230,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.needsUpdate = true;
  return t;
}

export default function KnowledgeTerrain({ height = 600 }: { height?: number }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { t } = useI18n();
  const [data, setData] = useState<Terrain | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [mode, setMode] = useState<'3d' | '2d'>('3d');
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const [hover, setHover] = useState<{ mx: number; my: number; p: TPoint } | null>(null);

  const months = useMemo(() => {
    if (!data) return [] as string[];
    const set = new Set<string>();
    for (const p of data.points) if (p.date) set.add(p.date.slice(0, 7));
    return [...set].sort();
  }, [data]);
  const [monthIdx, setMonthIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const applyCutoffRef = useRef<((cutoff: string) => void) | null>(null);

  useEffect(() => {
    let off = false;
    fetch('/api/terrain')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('not built'))))
      .then((d: Terrain) => !off && setData(d))
      .catch(() => !off && setErr(t('knowledge.terrainNotBuilt')));
    return () => {
      off = true;
    };
  }, [t]);

  // Default the scrubber to "all" once months are known.
  useEffect(() => {
    if (months.length) setMonthIdx(months.length - 1);
  }, [months]);

  // ---- three.js scene (built once per dataset) ----
  useEffect(() => {
    if (!data || !wrapRef.current) return;
    const wrap = wrapRef.current;
    let W = wrap.clientWidth;
    const H = height;

    const [minx, miny, maxx, maxy] = data.bounds;
    const sx = (v: number) => ((v - minx) / Math.max(maxx - minx, 1e-6) - 0.5) * SIZE;
    const sy = (v: number) => ((v - miny) / Math.max(maxy - miny, 1e-6) - 0.5) * SIZE;

    // Separable Gaussian kernel for the KDE.
    const kern: number[] = [];
    const R = Math.ceil(SIGMA * 3);
    for (let i = -R; i <= R; i++) kern.push(Math.exp(-(i * i) / (2 * SIGMA * SIGMA)));
    const ksum = kern.reduce((a, b) => a + b, 0);
    const blur = (src: Float32Array) => {
      const h = new Float32Array(GRID * GRID);
      for (let y = 0; y < GRID; y++)
        for (let x = 0; x < GRID; x++) {
          let s = 0;
          for (let k = -R; k <= R; k++) s += src[y * GRID + Math.min(GRID - 1, Math.max(0, x + k))] * kern[k + R];
          h[y * GRID + x] = s / ksum;
        }
      const o = new Float32Array(GRID * GRID);
      for (let y = 0; y < GRID; y++)
        for (let x = 0; x < GRID; x++) {
          let s = 0;
          for (let k = -R; k <= R; k++) s += h[Math.min(GRID - 1, Math.max(0, y + k)) * GRID + x] * kern[k + R];
          o[y * GRID + x] = s / ksum;
        }
      return o;
    };
    const densityFor = (pts: TPoint[]) => {
      const grid = new Float32Array(GRID * GRID);
      for (const p of pts) {
        const gx = Math.min(GRID - 1, Math.max(0, Math.round(((sx(p.x) / SIZE) + 0.5) * (GRID - 1))));
        const gy = Math.min(GRID - 1, Math.max(0, Math.round(((sy(p.y) / SIZE) + 0.5) * (GRID - 1))));
        grid[gy * GRID + gx] += 1;
      }
      return blur(grid);
    };
    // Fixed normalizer from the FULL corpus, so the terrain GROWS over the
    // timeline instead of rescaling to full height at every step.
    const fullDens = densityFor(data.points);
    let fullMax = 1e-6;
    for (const v of fullDens) fullMax = Math.max(fullMax, v);
    const heightFn = (dens: Float32Array) => (wx: number, wz: number) => {
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

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    renderer.domElement.style.borderRadius = '12px';
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.touchAction = 'none';
    wrap.style.overscrollBehavior = 'none';
    wrap.appendChild(renderer.domElement);

    const labelRenderer = new CSS2DRenderer();
    labelRenderer.setSize(W, H);
    labelRenderer.domElement.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;';
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
        uLine: { value: new THREE.Color('#7fd6e6') },
        uBase: { value: new THREE.Color('#0f151c') },
        uPeak: { value: new THREE.Color('#183245') },
      },
      vertexShader: `varying float vH; void main(){ vH=position.y; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
      fragmentShader: `
        uniform float uMaxH; uniform vec3 uLine; uniform vec3 uBase; uniform vec3 uPeak; varying float vH;
        void main(){
          float h=clamp(vH/uMaxH,0.0,1.0);
          float g=h*26.0; float fp=fract(g); float dist=min(fp,1.0-fp);
          float aa=fwidth(g)*1.3+1e-4; float edge=1.0-smoothstep(0.0,aa,dist);
          vec3 terrain=mix(uBase,uPeak,h);
          gl_FragColor=vec4(mix(terrain,uLine,edge*(0.3+0.7*h)),1.0);
        }`,
    });
    scene.add(new THREE.Mesh(geo, terrainMat));

    // ---- points (full-size buffer, drawn up to the visible count) ----
    const pgeo = new THREE.BufferGeometry();
    const parr = new Float32Array(data.points.length * 3);
    pgeo.setAttribute('position', new THREE.BufferAttribute(parr, 3));
    const pmat = new THREE.PointsMaterial({
      size: 4.5, map: glowTexture(), transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, color: 0x9fd6ea, sizeAttenuation: true,
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

    // ---- theme labels ----
    const labelObjs = data.themes
      .slice()
      .sort((a, b) => b.count - a.count)
      .slice(0, 16)
      .map((th) => {
        const el = document.createElement('div');
        el.textContent = th.label;
        el.style.cssText =
          'color:rgba(233,230,224,0.94);font:600 12px system-ui;text-shadow:0 1px 3px rgba(0,0,0,0.85);white-space:nowrap;';
        const obj = new CSS2DObject(el);
        scene.add(obj);
        return { th, el, obj };
      });

    // Interaction state, hoisted so `applyCutoff` can reset a stale hover when
    // the visible set shrinks under it.
    let hoverSlot = -1;
    let downX = 0;
    let downY = 0;

    // ---- rebuild terrain/points/labels for a date cutoff ----
    const applyCutoff = (cutoff: string) => {
      const vis = data.points.filter((p) => !p.date || p.date <= cutoff);
      const dens = densityFor(vis);
      const hAt = heightFn(dens);
      for (let i = 0; i < gpos.count; i++) gpos.setY(i, hAt(gpos.getX(i), gpos.getZ(i)));
      gpos.needsUpdate = true;
      geo.computeVertexNormals();

      visibleIdx.length = 0;
      vis.forEach((p) => {
        const wx = sx(p.x);
        const wz = sy(p.y);
        const slot = visibleIdx.length;
        parr[slot * 3] = wx;
        parr[slot * 3 + 1] = hAt(wx, wz) + 1.6;
        parr[slot * 3 + 2] = wz;
        visibleIdx.push(data.points.indexOf(p));
      });
      pgeo.setDrawRange(0, vis.length);
      pgeo.attributes.position.needsUpdate = true;

      // The hovered/marked point may have just dropped out of the visible set;
      // clear it so `onClick` can't read a now-out-of-range `visibleIdx` slot.
      hoverSlot = -1;
      mark.visible = false;
      setHover(null);

      const seen = new Set(vis.map((p) => p.theme));
      for (const l of labelObjs) {
        if (seen.has(l.th.label)) {
          l.el.style.display = '';
          const wx = sx(l.th.cx);
          const wz = sy(l.th.cy);
          l.obj.position.set(wx, hAt(wx, wz) + 6, wz);
        } else {
          l.el.style.display = 'none';
        }
      }
    };
    applyCutoff(FULL);
    applyCutoffRef.current = applyCutoff;

    // ---- interaction (click-vs-drag) ----
    const ray = new THREE.Raycaster();
    ray.params.Points = { threshold: 2.4 };
    const ndc = new THREE.Vector2();
    const pick = (ev: PointerEvent) => {
      // offsetX/Y are canvas-relative and reflow-free — avoids the layout
      // thrash of getBoundingClientRect() on every pointermove. W/H track the
      // canvas size via the ResizeObserver below.
      ndc.x = (ev.offsetX / W) * 2 - 1;
      ndc.y = -(ev.offsetY / H) * 2 + 1;
      ray.setFromCamera(ndc, camera);
      const hits = ray.intersectObject(points, false);
      const hit = hits.find((h) => (h.index ?? -1) < visibleIdx.length);
      if (hit) {
        hoverSlot = hit.index ?? -1;
        const p = data.points[visibleIdx[hoverSlot]];
        mark.visible = true;
        (mark.geometry.attributes.position as THREE.BufferAttribute).copyArray([
          parr[hoverSlot * 3], parr[hoverSlot * 3 + 1], parr[hoverSlot * 3 + 2],
        ]);
        mark.geometry.attributes.position.needsUpdate = true;
        setHover({ mx: ev.offsetX, my: ev.offsetY, p });
        renderer.domElement.style.cursor = 'pointer';
      } else {
        hoverSlot = -1;
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
      if (hoverSlot < 0 || hoverSlot >= visibleIdx.length) return;
      if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) return; // a drag, not a click
      const p = data.points[visibleIdx[hoverSlot]];
      if (!p?.sha) return; // pack not in the index → no /library page to open
      navigate(`/library/${encodeURIComponent(p.sha)}`);
    };
    renderer.domElement.addEventListener('pointermove', pick);
    renderer.domElement.addEventListener('pointerdown', onDown);
    renderer.domElement.addEventListener('click', onClick);

    // ---- loop (tween camera only on 2D/3D switch) ----
    let raf = 0;
    const t3d = new THREE.Vector3(0, HEIGHT * 3.2, SIZE * 0.82);
    const t2d = new THREE.Vector3(0, SIZE * 1.15, 0.001);
    let appliedMode: '2d' | '3d' = '3d';
    let tweening = false;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      if (modeRef.current !== appliedMode && !tweening) {
        tweening = true;
        controls.enabled = false;
        controls.maxPolarAngle = modeRef.current === '2d' ? 0.35 : Math.PI * 0.49;
      }
      if (tweening) {
        const want = modeRef.current === '2d' ? t2d : t3d;
        camera.position.lerp(want, 0.12);
        camera.lookAt(controls.target);
        if (camera.position.distanceTo(want) < 1.5) {
          camera.position.copy(want);
          tweening = false;
          appliedMode = modeRef.current;
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
    });
    ro.observe(wrap);

    return () => {
      applyCutoffRef.current = null;
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
      renderer.dispose();
      wrap.removeChild(renderer.domElement);
      wrap.removeChild(labelRenderer.domElement);
      labelObjs.forEach((l) => l.obj.removeFromParent());
    };
  }, [data, height, navigate]);

  // ---- drive the terrain from the scrubber ----
  const atLatest = monthIdx >= months.length - 1;
  useEffect(() => {
    if (!applyCutoffRef.current || !months.length) return;
    applyCutoffRef.current(atLatest ? FULL : `${months[monthIdx]}-31`);
  }, [monthIdx, months, atLatest]);

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

  if (err) return <div className="graph-caption" style={{ padding: '2rem 0' }}>{err}</div>;

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%', height }}>
      <div style={{ position: 'absolute', top: 10, left: 12, zIndex: 2, color: 'rgba(233,230,224,0.6)', font: '12px system-ui', pointerEvents: 'none' }}>
        {data
          ? t('knowledge.terrainHud', { notes: data.point_count, themes: data.themes.length })
          : t('knowledge.terrainLoading')}
      </div>
      <button
        type="button"
        onClick={() => setMode((m) => (m === '3d' ? '2d' : '3d'))}
        style={{ position: 'absolute', top: 10, right: 12, zIndex: 2, cursor: 'pointer', background: 'rgba(20,24,30,0.9)', color: '#e9e6e0', border: '1px solid rgba(120,180,205,0.4)', borderRadius: 8, padding: '5px 12px', font: '12px system-ui' }}
      >
        {mode === '3d' ? '2D' : '3D'}
      </button>

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
          <div style={{ color: '#7fd6e6', fontSize: 11, marginBottom: 3 }}>
            {hover.p.theme}{hover.p.date ? ` · ${hover.p.date}` : ''}
          </div>
          <div style={{ lineHeight: 1.35 }}>{hover.p.title}</div>
        </div>
      )}
    </div>
  );
}
