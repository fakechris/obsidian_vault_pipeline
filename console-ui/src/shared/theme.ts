export const COLORS = {
  background: '#0f1117',
  claim: '#c4b5fd',
  unit: '#93c5fd',
  source: '#86efac',
  edge: '#475569',
  edgeCites: '#a78bfa',
  edgeExtracted: '#67e8f9',
  text: '#e2e8f0',
  textMuted: '#94a3b8',
  panelBg: 'rgba(15, 17, 23, 0.92)',
  panelBorder: 'rgba(148, 163, 184, 0.15)',
  highlight: '#fbbf24',
} as const;

export const BLOOM = {
  strength: 0.6,
  radius: 0.4,
  threshold: 0.55,
} as const;

export const GRAPH = {
  particleCount: 3,
  particleSpeed: 0.003,
  particleWidth: 1.2,
  linkOpacity: 0.45,
  linkWidth: 0.6,
  linkWidthCites: 1.0,
  cooldownTime: 5000,
  nodePulseSpeed: 0.0015,
  nodePulseRange: 0.08,
  autoRotateSpeed: 0.15,
  starCount: 800,
  chargeStrength: -120,
  linkDistance: 40,
} as const;

export const NODE_COLORS: Record<string, { main: number; emissive: number; emissiveIntensity: number }> = {
  claim:  { main: 0xc4b5fd, emissive: 0x8b5cf6, emissiveIntensity: 0.6 },
  unit:   { main: 0x93c5fd, emissive: 0x3b82f6, emissiveIntensity: 0.4 },
  source: { main: 0x86efac, emissive: 0x22c55e, emissiveIntensity: 0.4 },
};

export const EDGE_COLORS: Record<string, number> = {
  cites: 0xa78bfa,
  extracted_from: 0x67e8f9,
};

export function nodeColor(type: string): string {
  switch (type) {
    case 'claim': return COLORS.claim;
    case 'unit': return COLORS.unit;
    case 'source': return COLORS.source;
    default: return COLORS.text;
  }
}
