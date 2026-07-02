// Visual identity carried over from the previous console (shared/theme.ts):
// claim purple / unit blue / source green, amber highlight, dark bg.

export const COLORS = {
  background: '#0f1117',
  claim: '#c4b5fd',
  claimDeep: '#8b5cf6',
  unit: '#93c5fd',
  unitDeep: '#3b82f6',
  source: '#86efac',
  sourceDeep: '#22c55e',
  edge: '#475569',
  edgeCites: '#a78bfa',
  edgeExtracted: '#67e8f9',
  text: '#e2e8f0',
  textMuted: '#94a3b8',
  highlight: '#fbbf24',
} as const;

export const EDGE_COLORS: Record<string, string> = {
  cites: COLORS.edgeCites,
  extracted_from: COLORS.edgeExtracted,
  related: COLORS.edge,
};

// Distinct, evenly-spaced hues for community coloring (from the previous
// graph2d.ts). Cluster 0 (isolated) renders gray.
export const CLUSTER_HUES = [
  265, 210, 145, 35, 0, 320, 175, 50, 240, 110, 300, 20,
] as const;

export function clusterColor(cluster: number | undefined, light = 68): string {
  if (!cluster || cluster <= 0) return '#64748b';
  const h = CLUSTER_HUES[(cluster - 1) % CLUSTER_HUES.length];
  return `hsl(${h} 65% ${light}%)`;
}

export function nodeColor(type: string): string {
  switch (type) {
    case 'claim':
      return COLORS.claim;
    case 'unit':
      return COLORS.unit;
    case 'source':
      return COLORS.source;
    default:
      return COLORS.text;
  }
}
