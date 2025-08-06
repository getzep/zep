export interface Node {
  uuid: string;
  name: string;
  summary?: string;
  labels?: string[];
  attributes?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface Edge {
  uuid: string;
  source_node_uuid: string;
  target_node_uuid: string;
  type: string;
  name: string;
  fact?: string;
  episodes?: string[];
  created_at: string;
  updated_at: string;
  valid_at?: string;
  expired_at?: string;
  invalid_at?: string;
}

export interface RawTriplet {
  sourceNode: Node;
  edge: Edge;
  targetNode: Node;
}

export interface GraphNode extends Node {
  id: string;
  value: string;
  primaryLabel?: string;
}

export interface GraphEdge extends Edge {
  id: string;
  value: string;
}

export interface GraphTriplet {
  source: GraphNode;
  relation: GraphEdge;
  target: GraphNode;
}

export interface IdValue {
  id: string;
  value: string;
}

// Graph visualization types
export interface GraphNode extends Node {
  id: string;
  value: string;
}

export interface GraphEdge extends Edge {
  id: string;
  value: string;
}

export interface GraphTriplet {
  source: GraphNode;
  relation: GraphEdge;
  target: GraphNode;
}

// Popup content types for UI
export interface NodePopupContent {
  id: string;
  node: Node;
}

export interface EdgePopupContent {
  id: string;
  source: Node;
  relation: Edge;
  target: Node;
}
