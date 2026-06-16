export interface GraphNode {
  id: string;
  type: 'claim' | 'unit' | 'source';
  label: string;
  theme?: string;
  degree?: number;
  strength?: string;
  case_id?: string;
  url?: string;
  cluster?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: 'cites' | 'extracted_from';
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface FlowLink {
  from: string;
  to: string;
  value: number;
  label: string;
}

export interface FlowData {
  stages: string[];
  flows: FlowLink[];
}

export interface CitationDetail {
  unit_id: string;
  unit_text: string;
  quote: string;
  resolved_line: number;
  case_id: string;
  source_title: string;
  source_url: string;
  source_sha256: string;
}

export interface ClaimDetail {
  claim_id: string;
  claim: string;
  theme: string;
  strength: string;
  citations: CitationDetail[];
}

export interface ProgressEvent {
  source: string;
  stage: string;
  status: string;
  index?: number;
  total?: number;
  units?: number;
  cards?: number;
}

export interface CompleteEvent {
  run_id: string;
  succeeded: number;
  failed: number;
}

export interface SearchResult {
  id: string;
  kind: string;
  label: string;
  score?: number;
}
