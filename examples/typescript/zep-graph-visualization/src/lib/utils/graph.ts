import type {
  Node,
  Edge,
  GraphNode,
  GraphEdge,
  RawTriplet,
  GraphTriplet,
} from "@/lib/types/graph";

export function toGraphNode(node: Node): GraphNode {
  const primaryLabel =
    node.labels?.find((label) => label != "Entity") || "Entity";

  return {
    id: node.uuid,
    value: node.name,
    uuid: node.uuid,
    name: node.name,
    created_at: node.created_at,
    updated_at: node.updated_at,
    attributes: node.attributes,
    summary: node.summary,
    labels: node.labels,
    primaryLabel,
  };
}

export function toGraphEdge(edge: Edge): GraphEdge {
  return {
    id: edge.uuid,
    value: edge.name,
    ...edge,
  };
}

export function toGraphTriplet(triplet: RawTriplet): GraphTriplet {
  return {
    source: toGraphNode(triplet.sourceNode),
    relation: toGraphEdge(triplet.edge),
    target: toGraphNode(triplet.targetNode),
  };
}

export function toGraphTriplets(triplets: RawTriplet[]): GraphTriplet[] {
  return triplets.map(toGraphTriplet);
}

export function createTriplets(edges: Edge[], nodes: Node[]): RawTriplet[] {
  // Create a Set of node UUIDs that are connected by edges
  const connectedNodeIds = new Set<string>();
  
  // Create triplets from edges
  const edgeTriplets = edges
    .map((edge) => {
      const sourceNode = nodes.find(
        (node) => node.uuid === edge.source_node_uuid
      );
      const targetNode = nodes.find(
        (node) => node.uuid === edge.target_node_uuid
      );

      if (!sourceNode || !targetNode) return null;

      // Add source and target node IDs to connected set
      connectedNodeIds.add(sourceNode.uuid);
      connectedNodeIds.add(targetNode.uuid);

      return {
        sourceNode,
        edge,
        targetNode,
      };
    })
    .filter(
      (t): t is RawTriplet =>
        t !== null && t.sourceNode !== undefined && t.targetNode !== undefined
    );
  
  // Find isolated nodes (nodes that don't appear in any edge)
  const isolatedNodes = nodes.filter(node => !connectedNodeIds.has(node.uuid));
  
  // For isolated nodes, create special triplets
  const isolatedTriplets: RawTriplet[] = isolatedNodes.map(node => {
    // Create a special marker edge for isolated nodes
    const virtualEdge: Edge = {
      uuid: `isolated-node-${node.uuid}`,
      source_node_uuid: node.uuid,
      target_node_uuid: node.uuid,
      // Use a special type that we can filter out in the Graph component
      type: "_isolated_node_",
      name: "", // Empty name so it doesn't show a label
      created_at: node.created_at,
      updated_at: node.updated_at
    };
    
    return {
      sourceNode: node,
      edge: virtualEdge,
      targetNode: node
    };
  });
  
  // Combine edge triplets with isolated node triplets
  return [...edgeTriplets, ...isolatedTriplets];
}
